# Plan — Code-Level Security Remediation (v2)

_Restores the "implement the fixes, not just report them" capability that decision
D1 (`docs/M3-DECISIONS.md`) deliberately cut for the hackathon. The hackathon is
over; the brief is now a complete, safe tool._

> **v2 — revised after two adversarial reviews** (safety/correctness + Foundry-IQ/test
> strategy). Both found that v1's "provably safe / auto-apply" framing was not earned:
> the scanner is a coarse, directory-only, 50-finding-capped line-regex that
> over-matches, the executor silently drops content-less actions, and the KB function
> isn't importable. v2 corrects all of these and **drops the "silent auto-apply" idea
> entirely** — every code edit is now a confirmed diff. Verified findings are in §10.

---

## 1. What exists today (and the gap)

| Layer | Today | Gap |
|-------|-------|-----|
| Detect code issues | ✅ `security_scan.py` finds 6 types (hardcoded_secret, code_injection/eval, sql_injection, tls_verification_disabled/`verify=False`, debug_enabled/`debug=True`, shell_injection_risk/`shell=True`) | — |
| Fix **config** issues | ✅ writes `.gitignore`, `.cursorignore` | — |
| Fix **code** issues | ❌ only generates `SECURITY.md` audit (D1: report-only) | **This plan** |
| Grounding | ✅ Azure AI Search KB + `kb/security-patterns/*.md` cover all 6 types with before/after code | Not yet used to generate fixes |

The writer today (`fs_tools.write_file`) only does **whole-file creates**. Fixing
source means **targeted in-place edits to existing files** — categorically riskier.
D1 called auto-editing user code a "scope trap." This plan's center of gravity is
making that operation safe *given the scanner we actually have* — which is a coarse
oracle, not a precise one (§10).

---

## 2. Core stance: no silent auto-apply for code

**Every code edit is presented as a unified diff and requires explicit, per-fix
confirmation. Code fixes default to UNCHECKED.** There is no "apply automatically"
path for source code — the human is always the final gate. This is the honest answer
to D1: we are not trusting a regex scanner to silently rewrite someone's code.

The "tiers" below therefore describe **how a proposed patch is generated**, not
whether it is auto-applied:

| Tier | Finding types | How the patch is generated | Availability |
|------|---------------|----------------------------|--------------|
| **A — Offline transform** | `tls_verification_disabled`, `debug_enabled` | Deterministic, **idiom-guarded** transform in `code_fixes.py`. Only fires when the line matches a recognized risky idiom (e.g. `requests`/`httpx`/`session` call for `verify`; `.run(`/`uvicorn`/`app.run` for `debug`). **If the idiom isn't recognized → declines → stays report-only.** | Local + Foundry |
| **B — KB-grounded LLM patch** | `sql_injection`, `code_injection` (eval/exec), `shell_injection_risk` | Semantic: needs to understand the query/driver/command. One **batched** remediator run, grounded in KB chunks. | Foundry only |
| **C — Assisted + mandatory rotation** | `hardcoded_secret` | Propose env-var wiring + `.env.example` stub, but **always** emit a blocking "rotate this credential" follow-up. Never claimed "fixed." | Foundry only |

Why idiom-guarding matters (verified, §10): the scanner flags `self.debug = True`,
`dict(debug=True)`, and `self.verify = False` — contexts where a blind token-flip
would corrupt unrelated code. The transform must re-check the *real source line* and
only act on idioms it recognizes; everything else falls through to the audit report.

Why `shell=True` is Tier B not A: `shell=True`→`shell=False` *breaks* a string
command — it needs the command restructured into an argument list (semantic).

Tiers B/C require Foundry. With no Azure you still get Tier-A proposals + the audit
report — graceful degradation.

---

## 3. Prerequisite refactors (must land first — they are NOT free)

Both reviews found that v1 assumed capabilities the code doesn't have. These are
explicit Phase-0 work items:

1. **`scan_file(path)` entry point** (`security_scan.py`). Today `scan_security_patterns`
   requires a directory, `rglob`s the whole tree, and **caps at 50 findings**
   (`_MAX_FINDINGS`). Verification needs to scan a *single file* with **no cap**.
   Refactor the per-file body (the inner loop) into `scan_file(path) -> {findings}`,
   and have `scan_security_patterns` call it. Verification uses `scan_file`.
2. **Module-level `kb_search(query, stack_filter=None, threat_filter=None)`**
   (`researcher.py`). Today `search_security_kb` is a **closure** inside
   `_make_tool_handlers()` taking a single `args` dict — not importable. Extract it
   (and `search_web`) to module level; rewrite the handlers as
   `lambda args: kb_search(**args)`. The remediator and the capture script both
   import this.
3. **Executor skip-logic + `_is_writable` fix.** `executor.run` drops any action
   with `content is None` as "manual edit required" (`executor.py:215`), and
   `run_with_foundry` pre-filters on `content is not None` (`executor.py:260-265`).
   A `remediate` action has no `content`, so it is **dead on arrival**. Change the
   skip to `action == "improve" or (content is None and action != "remediate")`,
   update the Foundry filter identically, and update `_is_writable` in
   `mcp_server.py:68` so remediate actions surface as elicitation checkboxes.

---

## 4. The safety harness

Every code edit passes the same gates. **No fix is offered to the user until it has
been proven, on a scratch copy, to remove the finding without breaking the file.**

1. **Line-addressed, content-checked edits — NOT snippet string-matching.**
   v1's "unique string match" fails twice: identical risky lines recur
   (`app.run(debug=True)` twice in a file), and the scanner's `snippet` is
   `line.strip()`, **truncated at 120 chars** and **redacted for secrets** — so it
   often isn't even a substring of the source. Instead: the finding carries `line`;
   the harness **re-reads that exact line from the file**, confirms it still contains
   the expected pattern, and replaces *that line*. Ambiguity is resolved by line
   number, not by hoping the line is unique.
2. **Backup, versioned.** `write_file` overwrites `<file>.bak` unconditionally, so a
   second run would clobber the pristine original. Use versioned backups
   (`.bak`, `.bak.1`, …) or refuse to overwrite an existing `.bak`. **Also prefer a
   clean git tree:** if the target file is git-tracked and clean, git is the real
   safety net; if the working tree is dirty or it's not a repo, warn before editing.
3. **Pre-apply verification (scratch copy, before the user sees the fix):**
   - the patched file still **parses** — Python via `compile()`; **JS/TS is
     report-only in v1** (no reliable offline parser — see Decision 5);
   - **`scan_file` on the patched file shows the finding gone** (cap-free, single
     file — see prereq 1);
   - **no NEW findings**, compared **scoped to the edited file** by
     `(finding_type, normalized_snippet)` **ignoring line numbers** (multi-line
     patches shift line numbers, so a line-based compare would false-positive).
   Patches failing any check are dropped to the audit report, never offered.
4. **Diff + confirm gate.** Every fix shown as a unified diff; confirmed per-fix.
   CLI prints diffs and asks; MCP extends the elicitation checkboxes. **Default
   UNCHECKED.**
5. **Post-apply verification.** Re-run `scan_file` to confirm; (opt-in, sandboxed,
   timeout-bounded) run `conventions.test_command`. On failure, one-command rollback
   from the versioned backup.
6. **The audit never regresses.** `SECURITY.md` is still generated for everything not
   fixed (or whose patch failed verification). Remediation is **additive**.

Honest limits of these gates (§10): the scanner is regex-only and can be fooled
(a fix that wraps a value in a string, a `# nosec` comment, or an over-matched
idiom can make "finding cleared" read true without a real fix). That residual risk
is exactly why **gate 4 (human confirms the diff) is mandatory and defaults off** —
the harness narrows the set; the human makes the call.

---

## 5. Architecture & components

Flow today: `Scan → Research → Plan → Confirm → Execute(write_file) → Verify`.
Remediation adds a new **`remediate` action type** alongside `create`/`update`/`improve`.

| File | Change |
|------|--------|
| `src/tools/security_scan.py` | **Refactor:** add `scan_file(path)` (prereq 1). |
| `src/agents/researcher.py` | **Refactor:** extract `kb_search()` (prereq 2). |
| `src/tools/code_fixes.py` | **NEW.** Idiom-guarded Tier-A transforms; each returns a proposed line or `None`. Pure, unit-testable. |
| `src/tools/remediation.py` | **NEW.** Harness: `apply_code_fix(path, file, line, expected, proposed)` (re-read line, content-check, versioned backup, `_is_safe_path`), `make_unified_diff`, `verify_code_fix(path, file, finding_type)` (parse + `scan_file` re-scan + no-new-findings), `rollback`. |
| `src/agents/remediator.py` | **NEW (Foundry).** **One batched run** for all Tier-B/C findings → list of patches, each grounded in KB chunks fetched via `kb_search` (direct call — §6). Returns `kb_citations`. |
| `src/agents/planner.py` | Emit `remediate` actions; keep emitting `SECURITY.md` for the rest. Ensure `_normalize_ranks` runs **before** the elicitation schema is built (it does today; the new injection path must respect that ordering). |
| `src/agents/executor.py` | Skip-logic fix (prereq 3); handle `remediate` by delegating to `apply_code_fix`; record in the same ledger. **Executor stays the orchestration/ledger owner; `apply_code_fix` is the low-level writer it calls** (resolves v1's "who writes" contradiction). |
| `src/agents/verifier.py` | For remediations, verify via `scan_file` (finding cleared) instead of `verify_file` section checks. |
| `src/orchestrator.py` | Thread remediation through existing `run_plan_phase`/`run_apply_phase` — same gate, no new phase. |
| `src/mcp_server.py` | `_is_writable` update (prereq 3); code fixes in elicitation, default unchecked. |
| `src/cli.py` | Diff preview + per-fix confirm; `--run-tests` opt-in. |

### `remediate` action shape

```jsonc
{
  "rank": 1, "tool": "security", "action": "remediate",
  "file": "app/main.py", "line": 24,
  "finding_type": "sql_injection", "severity": "high", "tier": "assisted",
  "expected_line": "    cursor.execute(f\"SELECT ... '{customer_name}'\")",  // re-read & checked
  "proposed_line": "    cursor.execute(\"SELECT ... = ?\", (customer_name,))",
  "patch": "<unified diff>",
  "rationale": "Parameterised query — values bound, never interpolated.",
  "kb_citations": [{"title": "CWE-89: SQL Injection", "url": "..."}],
  "requires_followup": null,            // "Rotate this credential" for secrets
  "confidence": "high",
  "verification": { "file_parses": true, "finding_cleared": true,
                    "no_new_findings": true, "tests_pass": null }
}
```

---

## 6. Using the Foundry IQ layer (grounding)

**Execution model: direct call, not a free-roaming agent loop.** The remediator
calls `kb_search(...)` directly in Python (deterministic, testable, cheap), then
passes the returned chunks to one LLM run as context to produce patches. This is the
only model under which we can assert "the right query was built" in tests.

- **Generation:** for each Tier-B/C finding, build a query from finding type +
  detected stack (e.g. `"sql injection parameterised query fix"`, stack `fastapi`).
  Returned chunks ground the patch; their `source_url`/`source_title` become
  `kb_citations`. (Known limit: `threat_categories`/`stack_applicable_to` aren't
  filterable, so hints are folded into query text — already handled in `researcher.py`.)
- **Citation = soft signal, not a hard gate.** v1 made "must cite a source whose
  metadata matches the finding" an acceptance criterion. That's too strict: results
  are top-5 by relevance, tags are author-applied and not guaranteed per chunk, and
  `owasp_mappings` **isn't even in the `select` list** today. So: require that *a*
  citation is attached; surface metadata overlap as a confidence hint only. (If we
  want an OWASP hint, add `owasp_mappings` to `select` + the result dict first.)
- The IQ layer **grounds and cites**; the **safety harness (§4) decides acceptance.**

---

## 7. Test strategy (incl. the IQ layer)

### A. Offline / deterministic (runs in CI, no Azure)

- **Golden before/after fixtures** in `tests/fixtures/remediation/` — for **Tier A
  only** (deterministic). Plus idiom-guard negatives: `self.debug = True`,
  `dict(debug=True)` → transform **declines** (returns `None`).
- **`apply_code_fix` tests:** re-reads the line by number, content-check refuses a
  stale/mismatched line, versioned backup created, path traversal blocked, edits only
  the target line even when an identical line exists elsewhere.
- **Verification-harness tests** — the contract that actually protects Tier B
  (which is otherwise nondeterministic): feed **recorded real patches** (good *and*
  deliberately-broken: syntax error, introduces a new finding, doesn't clear the
  finding) through `verify_code_fix` and assert accept/reject. Capture a few real
  Tier-B generations once and replay them — this is the only offline coverage Tier B
  gets, and the plan says so plainly.
- **Behavior-preservation:** after fixing the `vulnerable-project` fixture, its own
  `tests/test_orders.py` still passes.
- **End-to-end local mode:** temp copy; only Tier-A proposals appear, nothing written
  without confirmation, Tier-B/C remain in `SECURITY.md`.

### B. Foundry IQ tests

1. **Remediator contract tests (KB mocked with *real captured* data).** A script
   `scripts/capture_kb_responses.py` (imports the new `kb_search`, so sequenced after
   prereq 2) queries the **real index once** per finding type → saves
   `tests/fixtures/kb_responses/<finding_type>.json`. Tests mock `kb_search` to
   return these and assert the remediator builds the right query and extracts
   citations. **Honest scope: this tests the remediator's *use* of IQ data — query
   construction + citation handling — NOT the live Azure Search path.** CI does not
   exercise the live IQ layer.
2. **Live IQ integration tests (opt-in, real coverage).** Gated with explicit
   `skipif` (not a bare marker, which doesn't skip):
   - KB-only tests: `skipif(not (AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_KEY))`.
   - Full generate→verify tests: additionally require `FOUNDRY_PROJECT_ENDPOINT` and
     `FOUNDRY_MODEL_DEPLOYMENT_NAME`.
   Register the `foundry` marker in `conftest.py`. These assert the live index
   returns chunks whose metadata/content match the finding (e.g. SQL-injection query
   → parameterised-query content). Run locally / in a gated job with secrets; **CI
   skips them cleanly.**

---

## 8. Phasing

| Phase | Deliverable | Status |
|-------|-------------|--------|
| **0 — Prerequisites** | `scan_file` refactor, executor/`_is_writable` skip-logic fix, remediation skeletons. (`kb_search` extraction deferred to Phase 2.) | ✅ **Done** |
| **1 — Tier A, offline** | `code_fixes.py` (idiom-guarded) + `remediation.py` harness + planner/executor/verifier wiring + diff display at the confirm gate + full offline test suite (34 new tests). Restores real code-fixing with zero Azure. | ✅ **Done** |
| **2 — Tier B/C, Foundry** | `kb_search` extraction + `remediator.py` (batched, direct `kb_search`) + capture script + contract tests + opt-in live-IQ tests + Foundry plan wiring. | ✅ **Done** (offline-verified; live LLM/Azure path needs creds) |
| **3 — UX & rollback** | Richer CLI diffs, opt-in sandboxed post-apply test run (`--run-tests`) + rollback UX. (Basic diff display + rollback fn already in.) | ⏸ |
| **4 — Docs & demo** | README (honest two-tier framing) + demo assets (the new "it fixes the code" moment). | ⏸ |

### Phase 0+1 — as built
- `security_scan.scan_file()` / `scan_text()` — single-file, cap-free scanning for verification.
- `code_fixes.py` — `fix_verify_false`, `fix_debug_true`, idiom-guarded; decline non-call contexts.
- `remediation.py` — `replace_line` (line-addressed, content-checked), `verify_patch` (parse + finding-cleared + no-new-findings, line-independent compare), `apply_code_fix` (versioned backup, traversal guard), `rollback`.
- `planner._build_remediation_actions` — emits only verified Tier-A `remediate` actions; SECURITY.md still lists every finding.
- `executor` handles `remediate` (delegates to `apply_code_fix`); `verifier` confirms via `scan_file`; `mcp_server` surfaces code fixes default-UNCHECKED.
- Verified e2e on `vulnerable-project`: `verify=False→True` + `debug=True→False` applied, findings cleared, **fixture's own pytest still green** (behavior preserved); Tier B/C correctly untouched.

### Phase 2 — as built
- `researcher.kb_search()` / `web_search()` — extracted to module level (was a closure);
  `_make_tool_handlers` now wraps them, so the Foundry tool loop is unchanged.
- `remediation.replace_block` — multi-line block edits (a Tier-B fix can add an import);
  `replace_line`/`apply_code_fix` now build on it (single-line == block-of-1).
- `agents/remediator.py` — pure/agent seam: `build_kb_query`, `fetch_kb_context`
  (one query per type, injectable for tests), `parse_patches`, `verify_and_build_actions`
  (every patch re-verified by `verify_patch`; Tier-C always gets a rotation follow-up;
  citations attached as a soft signal), and one **batched** `run_with_foundry` (direct
  KB call → single LLM run, not per-finding).
- `orchestrator._augment_with_remediations` — Foundry plan gets Tier A (deterministic)
  + Tier B/C (LLM); defensive (remediator failure → Tier-B/C stay in SECURITY.md);
  re-normalizes ranks.
- Tests: contract tests replay **recorded patches** (good/broken/stale) through the real
  harness — Tier-B/C offline coverage without an LLM; live-IQ tests gated by `skipif` on
  `AZURE_SEARCH_*` (4 skip cleanly in CI). `scripts/capture_kb_responses.py` + synthetic
  `tests/fixtures/kb_responses/*.json` (regenerate against the live index before relying
  on real citations).
- **Not exercised without credentials:** live Azure AI Search query + live LLM patch
  generation (the opt-in path). The generate→verify *contract* is covered offline.

### Live e2e validation (2026-06-25, real Azure/Foundry)
Ran the full Foundry chain (scan→research→plan→remediate→apply→verify) on a copy of
`vulnerable-project`. **Final result: 7/7 remediations applied, 0 errors, ALL code
findings cleared, fixture's own tests PASS.** Getting there surfaced 5 bugs that only
appear in the live full pipeline — all fixed + regression-tested:
1. `foundry_utils.get_last_assistant_message*` crashed on an empty assistant turn
   (`content[0]` IndexError) → skip empty turns (`_message_text`).
2. Foundry scan results are LLM-round-tripped and lose exact file/line → remediation
   couldn't locate code. Fix: re-derive code findings deterministically in
   `_augment_with_remediations` (D8 — findings are facts, not model output).
3. `executor.run_with_foundry` dropped `remediate` actions (content-is-None filter +
   LLM-write model). Fix: remediations apply+verify **deterministically in every mode**
   (`_apply_remediations_deterministically`); the LLM executor only writes configs.
4. Fixes that need an import compiled but NameError'd at runtime (`os.environ` with no
   `import os`). Fix: undefined-name guard in `verify_patch` + auto `ensure_imports`
   (safe stdlib allowlist), replayed at apply time via `add_imports`.
5. Multiple fixes in one file: an inserted import shifted later line numbers, so the
   content-drift guard refused them. Fix: `apply_code_fix` relocates the block by
   **content** (`locate_block`), not a stale line number.

Phase 1 alone restores real (deterministic, idiom-guarded, human-confirmed) code
fixing, fully offline and CI-tested — a safe, self-contained first milestone.

---

## 9. Decisions (CONFIRMED 2026-06-25)

1. ✅ **No silent auto-apply for code — all fixes are confirmed diffs, default
   unchecked.** _Confirmed._
2. ✅ **v1 fix scope: Tier A (verify/debug, idiom-guarded) + Tier B assisted in
   Foundry + Tier C secrets always assisted + rotation warning.** _Confirmed._
3. ✅ **Post-apply test run: off by default, `--run-tests`, sandboxed + timeout.**
   _Confirmed (Phase 3)._
4. ✅ **JS/TS fixes report-only in v1; Python-first.** _Confirmed — matches the
   Python `vulnerable-project` fixture._
5. ✅ **Batched `remediator` agent; executor stays orchestrator/ledger, delegating
   writes to `apply_code_fix`.** _Confirmed._

**Proceeding with Phase 0 + Phase 1** (prerequisite refactors + offline, idiom-guarded,
human-confirmed Tier-A fixer with full test suite). `kb_search` extraction is deferred
to Phase 2 (only the remediator needs it). Check-in before Phase 2 (Foundry/LLM tier).

---

## 10. Review findings incorporated (verified against code)

Confirmed by direct inspection / running the scanner:

- **Executor drops content-less actions** (`executor.py:215`, and the Foundry filter
  `:260-265`) → `remediate` is dead on arrival. → Prereq 3.
- **Scanner over-matches:** `self.debug = True`, `dict(debug=True)`, `self.verify = False`
  all flag (`\bdebug\s*=\s*True\b` / `\bverify\s*=\s*False\b`). → idiom-guarded
  transforms (§2), human-confirm gate (§4.4).
- **Scanner is directory-only and caps at 50 findings** (`is_dir()` check,
  `_MAX_FINDINGS = 50`). → `scan_file` prereq + scoped, cap-free verification (§4.3).
- **`search_security_kb` is a non-importable closure** taking `args: dict`
  (`researcher.py:131`). → `kb_search` extraction prereq.
- **`owasp_mappings` not in the search `select`** (`researcher.py:161`). → citation is
  a soft signal; add field only if we want the OWASP hint.
- **Snippet is `line.strip()`, truncated at 120 chars, secrets redacted**
  (`security_scan.py:116-122`). → edits keyed on line number + re-read, not snippet.
- **`write_file` clobbers `.bak`** (`fs_tools.py:465-468`); `.gitignore` has `*.bak`.
  → versioned backups + prefer-clean-git-tree.
- **Rate-limit history is real** (`PROGRESS.md`, `M3-DECISIONS.md`; backoff caps at
  3×, 30/60/90s in `foundry_utils.py`). → **one batched remediator run**, not per-finding.

### Open questions for you
- **JS/TS in the demo?** If yes, we accept weaker JS guarantees in v1 or invest in a
  real JS parser up front. If the demo is Python (the `vulnerable-project` fixture
  is), Python-first is clearly right.
- **Do the 32 KB sources actually tag chunks per finding-type** richly enough for the
  citation hint to be useful? The capture script (Phase 2) will answer this with real
  data before we lean on it.

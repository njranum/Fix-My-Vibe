# Performance — Investigation & Decision Record

_Living document. Started 2026-06-26 on branch `Performance-improvements`._

Goal: the full **Foundry (cloud) pipeline** is slow. Instrument first (Phase 0),
then optimise from real numbers rather than guesses.

---

## Phase 0 — Instrumentation (done)

Added `src/tracing.py`: a zero-overhead-when-off timing layer enabled by
`--trace` (or `FMV_TRACE=1`). Writes JSONL to `.fmv-traces/` and prints a
per-phase wall-time table (model round-trips, polls, local tool time,
provisioning). Wired into `orchestrator.py` (both modes) and `cli.py`. The
summary prints on exit via an `atexit` backstop, so a mid-run crash still
yields the breakdown.

### Baseline run — `tests/fixtures/demo-webapp` (3 problems), Foundry mode, o4-mini

```
PHASE            WALL  MODEL_RTs  POLLS   STATUS
scanner        37.98s          6     37   ok
researcher     18.13s          1     20   ok
planner       323.40s          1    400   TIMED OUT (max_iterations, never completed)
remediator     10.97s          1     11   ok
executor      128.84s          4    152   ok
verifier       11.94s          0      0   CRASHED (KeyError: 'relative_path')
TOTAL         531.28s         13    620
```

Total wall: **8m 51s**. Trace: `.fmv-traces/20260626-110830-foundry.jsonl`.

Headline: code-reading guesses (scanner/provisioning) were **wrong**. The cost
is concentrated in the **Planner (61%)** and **Executor (24%)**, and ~310s of
the wall clock is `time.sleep(0.5)` polling.

---

## Finding F1 — Planner times out generating file content (root cause found)

**Symptom:** Planner phase = 323s, 1 model round-trip, no tool calls, ends
`IN_PROGRESS` at the 400-iteration cap — i.e. the model never finished and was
killed. `parse_json_response` then works off an incomplete message.

**Cause:** `planner.run_with_foundry` (`src/agents/planner.py:708`) asks o4-mini
to **generate the full text of 5-6 config files** (CLAUDE.md, PROMPTS.md with
5-8 prompts, SECURITY.md, .gitignore, .cursorrules, .cursorignore) inside a
**single JSON** `ActionPlan`. For a reasoning model that means hundreds of
visible output tokens **plus** thousands of hidden reasoning tokens in one
generation. The existing code comment already conceded this ("takes well over
120 polling iterations") and bumped the cap 120 → 400; 400 is now also exceeded.

**Evidence (measured on demo-webapp, the SMALL 3-problem case):**

| | Local planner (`run`) | Foundry planner (`run_with_foundry`) |
|---|---|---|
| Output | 4 actions, **944 tokens** of file content | same content, regenerated as free-form text |
| Time | **5.7 ms** | **323 s, then timed out** |
| Quality | deterministic, stack-tailored | control-char garbage in SECURITY.md (`\x14`, `\x16`) |

Larger projects (more tools/findings) generate **more** files → an even larger
single-shot generation → worse.

**Key realisation:** the content is almost entirely **deterministic** and the
local path already builds it via `_generate_claude_md`, `_generate_cursorrules`,
`_generate_cursorignore`, `_generate_gitignore`, `_generate_security_md`,
`_generate_prompts_md`. The Foundry planner throws those away and pays an LLM to
reproduce them — slower, less reliable, and (for `.gitignore`/`.cursorignore`/
`SECURITY.md`) with zero reasoning value. `SECURITY.md` is explicitly a
structured render of deterministic scanner facts (see M3-DECISIONS D1).

### Decision D-P1 — Planner: deterministic file content, LLM for reasoning only

**Verdict: ✅ bypass the LLM for file-content generation; reserve the model (if
used at all) for the narrow reasoning layer — ranking/prioritisation rationale,
`plan_summary`, and any stack-specific "DO NOT" judgement.**

Rationale:
- Kills the dominant 323s cost **and** the timeout correctness bug in one move.
- Reuses generators that already exist and are tested locally.
- Fixes the garbage-output regression.
- Faithful to the project's actual thesis: the reasoning is the *prioritisation*,
  not the boilerplate. "Reasoning before action" is preserved; only the
  mechanical text generation moves back to Python.

Alternatives considered:
- **B — split into per-file LLM calls.** Smaller per call, but multiplies
  round-trips + provisioning, still slow, still lower quality. Rejected.
- **C — tune only** (lower reasoning effort, raise cap, shorter poll sleep).
  Band-aid; doesn't address "too much output," may still time out. Rejected as
  a fix (some of it still worth doing for the *other* phases — see F4).

Decision confirmed with owner (2026-06-29): **thin reasoning layer.** The rank
ORDER is a deterministic policy (security first → missing configs → prompts →
improvements) and must NOT go to the LLM — that would be paying Azure to run an
`if`. The Azure call is reserved for the prioritisation **rationale** (the "why",
project-specific) so the Planner stays a genuine Foundry reasoning agent doing
work that actually rewards a model. Rationale: keeps the multi-Azure-agent
showcase intact while removing the boilerplate-generation timeout.

### Implemented (2026-06-29) — `planner.run_with_foundry`

- File content + ranks: deterministic (`run()` + existing generators).
- One small Azure call (`_reason_about_priorities`): receives only the compact
  action list (rank/file/priority/reason) + one-line diagnosis — never full file
  contents — and returns a 2-4 sentence rationale (`prioritization_rationale` +
  `_reasoning_trace`). `max_iterations=60`.
- **Non-fatal LLM:** the plan is complete before the call; any failure/timeout
  just drops the rationale. Removes the old single-point-of-failure.
- Old 70-line `PLANNER_INSTRUCTIONS` (the timeout-causing "generate all files"
  prompt) deleted.

### Result — re-trace vs baseline (same fixture, Foundry, o4-mini)

```
PHASE         BEFORE      AFTER     STATUS
scanner        38.0s      43.5s     ok
researcher     18.1s      48.5s     ok (more KB+web calls this run)
planner       323.4s ✗     9.9s ✓   TIMEOUT -> COMPLETES  ← the fix
remediator     11.0s      17.1s     ok
executor      128.8s     116.1s     ok
verifier       11.9s ✗    47.3s ✓   CRASH -> COMPLETES     ← F3 fix
TOTAL         531.3s     282.4s     -47% wall; pipeline now finishes end-to-end
```

Planner: **323s → 9.9s** and no longer times out. F3 (verifier guard) holds —
the pipeline completed end-to-end for the first time (5 files written + app.py
remediated + verification ran). Total nearly halved. Trace:
`.fmv-traces/20260629-112523-foundry.jsonl`.

### F3 — Verifier crash: FIXED (2026-06-29)
`verifier._make_tool_handlers` now returns a structured error for missing
`relative_path`/`filename` instead of raising `KeyError`. A malformed tool call
can no longer abort the pipeline.

---

## Secondary findings (ranked, not yet actioned)

### F2 — Executor: now the #1 cost (116s) AND corrupts content
LLM-driven `write_file` calls. Two problems, one fix:
- **Cost:** 116s to write files whose content is already finalised in the plan.
- **Corruption (new evidence, 2026-06-29):** the executor LLM re-transcribes the
  plan's content into `write_file` arguments and mangles non-ASCII — em-dashes
  (`—`) become control char `\x14`. Verified: the deterministic generator output
  is clean (0 control chars); the on-disk file the LLM wrote had 6. So the LLM
  executor is actively *degrading* finalised content for no benefit.
**D-P2: deterministic config writes in Foundry mode — IMPLEMENTED (2026-06-29).**
`executor.run_with_foundry` now delegates to the deterministic `run()` (same path
as local mode); the LLM, tool definitions, and the old `EXECUTOR_INSTRUCTIONS`
prompt are gone. The plan already carries exact `content`, so the write ledger is
ground truth by construction.

Result (same fixture, Foundry): **executor 116s → 0.00s**, total **282s → 184s**.
Verification rose **3/5 → 5/5** — because the written content is no longer
corrupted (confirmed: 0 control chars in all four files, was 6 in SECURITY.md
alone). So removing the executor LLM was a win on **all three** axes: speed,
correctness, and output quality. Trace: `.fmv-traces/20260629-113939-foundry.jsonl`.

### F3 — Verifier crashes on empty tool args (correctness, blocks completion)
Model called `verify_file({})`; handler does unguarded `args["relative_path"]`
(`src/agents/verifier.py:107`) → `KeyError` → aborts the whole pipeline **after
files were written**. Quick fix: guard the handler (return a structured error
when `relative_path` is missing) so a malformed tool call can't kill the run.
Independent of perf; worth doing first as it currently prevents clean completion.

### F4 — Polling: ~310s of `time.sleep(0.5)` across 620 polls
`foundry_utils._run_once` sleeps a flat 0.5s between status gets. Much overlaps
genuine model compute, but on completed phases it is real waste. Candidate:
shorter initial interval + exponential backoff. Smaller win than F1/F2; do after.

### F5 — Provisioning: ~24s creating/deleting a fresh agent per run
Six `create_agent` + `delete_agent` round-trips per pipeline. Agent definitions
are static → create once / reuse (or pre-provision by ID). Smallest of the wins.

### F6 — Scanner: deterministic detection — IMPLEMENTED (2026-06-29)
5 tools, all local, combined 0.01s; the rest was LLM orchestration of a FIXED
sequence (the instructions literally said "follow exactly") — no decision to make.
`scanner.run_with_foundry` now delegates to the deterministic `run()`; the
`SCANNER_INSTRUCTIONS` prompt, tool definitions, and handlers (~155 lines) are
deleted. Bonus: the LLM round-trip used to lose exact file/line in findings (the
orchestrator had a workaround re-deriving them) — the deterministic path is exact.
Result: **scanner 43s → 0.00s**, total **184s → 97.5s**.

---

## Through-line

~490s of the 531s is LLM time spent on work the local path does deterministically
in **~6 ms total**. The strategic question for the owner: **what does the cloud
reasoning actually add over local mode?** Right now the planner's timeout means
that phase delivers *negative* value (slower *and* incomplete). The optimisation
work (D-P1, D-P2, F6) converges on: keep the LLM only where there is genuine
reasoning, run everything mechanical in Python.

## Progress

```
              BASELINE   D-P1+F3   D-P2    F6
TOTAL wall      531s       282s     184s    97.5s   (-82% from baseline)
scanner          38s        43s      43s     0.0s ✓
planner         323s ✗      9.9s     19s     6.6s ✓
executor        129s       116s      0.0s    0.0s ✓
verifier      crash ✗      47s      48s     29s (5/5) ✓
researcher       18s        48s      54s     51s     (genuine reasoning — kept)
```

- ✅ Phase 0 instrumentation + crash-proof summary.
- ✅ F3 — verifier tool-handler guard (pipeline completes end-to-end).
- ✅ D-P1 — thin-reasoning-layer planner (323s→~7-19s, no timeout).
- ✅ D-P2 — deterministic Foundry executor (116s→0s; fixed content corruption; 5/5 verify).
- ✅ F6 — deterministic Scanner (43s→0s; exact findings).

**8m 51s → 1m 38s.** The pipeline now spends its time only where there is real
reasoning.

## F7 — Transient server errors aborted the whole pipeline — FIXED (2026-06-29)
Caught during demo-readiness testing: the Researcher hit a transient Azure
`server_error` ("Sorry, something went wrong.") and the entire run crashed with an
unhandled `RuntimeError` — `run_agent_with_tools` retried **rate limits only**.
For a live demo, a random Azure hiccup on any agent killing everything is the
worst failure mode.

Fix: `_run_once` now returns a `transient_error` signal for
`server_error`/`service_unavailable`, and `run_agent_with_tools` retries it (5s ×
attempt backoff, vs 30s for rate limits). Safe because every agent that still uses
an LLM is read-only / side-effect-free — the Executor writes deterministically, so
re-running a failed LLM agent has no double-write risk.

## Demo fixture & validation (2026-06-29)
`tests/fixtures/demo-shop` — purpose-built to demo Foundry IQ. 5 detected problems:
missing CLAUDE.md, exposed `.env`, hardcoded secret (`app.py:9`), SQL injection
(`app.py:17`), and Flask debug mode left on (`app.py`). Single tool (Claude Code).

Validated on the real cloud pipeline (o4-mini + Azure AI Search KB):
- **Foundry IQ exercised:** 5 grounded `search_security_kb` queries per run; the
  Remediator's fixes carry OWASP/CWE citations (e.g. "OWASP - Secrets Management
  Cheat Sheet") shown at the confirmation gate.
- **Interactive confirmation gate** (no `--yes`): full plan + coloured diffs +
  `[a/s/n]` works; "a" applied all.
- **Stability across 3 runs:** all completed, all checks passed (6/6 or 7/7),
  0 control-char corruption. Wall time 92-165s; the *number* of code remediations
  varies 1-2 run-to-run (the KB-grounded Tier-B/C remediator is an LLM, so whether
  it emits a verified in-place fix for the SQL injection vs. only reporting it in
  SECURITY.md is non-deterministic — both outcomes are correct).

Note for demo: fix count varies slightly run-to-run depending on remediator output;
all are verified before applying. (PROMPTS.md was later removed as an output — it was
the only action not tied to a detected problem; see below.)

### MCP plan caching (2026-07-02)
The Claude Code demo calls `scan_project` → `propose_fixes` → `apply_fixes`. Both
`propose_fixes` and `apply_fixes` ran the full diagnose+plan phase (Researcher +
Planner + Remediator), so the expensive LLM plan ran **twice** per demo (~155s each
observed). `mcp_server` now caches the plan per project, guarded by a file signature
(`_project_signature`): `apply_fixes` reuses the plan `propose_fixes` just computed
unless the project changed. Verified: 2nd `propose` on an unchanged project returns in
0.00s vs 155s, identical plan. Cuts a full plan phase off the demo (~6.5min → ~3.5min).

### PROMPTS.md output removed (2026-06-29)
The auto-generated PROMPTS.md "prompt library" was cut. Unlike every other action it
wasn't fixing a detected problem — it was prescribed whenever AI tools were present —
and its value (project conventions for the AI) is already covered by CLAUDE.md. Removed
from the planner (`run` + `_ensure_security_actions`), deleted `_generate_prompts_md`,
and dropped the now-unused `has_prompts_md` scanner field.

## Next immediate task
1. Verifier (~29s) — mostly mechanical file/section checks; deterministic
   verification already exists in local mode. Bypass unless the "does this read
   well / is it complete" judgement is wanted (it currently emits useful
   recommendations, so this one is a genuine judgement call — discuss before cutting).
2. F4/F5 — poll-interval backoff + agent reuse (smaller wins, do last).

Remaining live-LLM phases: **Researcher** (~51s, KB-vs-web judgement),
**Remediator** (~11s, code-fix reasoning), **Planner rationale** (~7s), and the
**Verifier** (pending decision). These are the agents that actually reason —
the Azure multi-agent showcase is intact, just no longer paying the model to do
Python's job.

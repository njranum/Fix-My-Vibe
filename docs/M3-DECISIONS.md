# M3 — Decision Record

_Living document. Updated as M3 work proceeds. Started 2026-06-10 (Day 4)._

Scope decisions for the M3 expansion (see `M3-Expanded-Functionality.md` for the
original proposal). Driving constraint: **deadline June 14, ~2.5 build days left,
and README + demo video + submission still outstanding.** A working recorded demo
beats every feature below.

---

## Scope verdicts

| Proposal | Verdict | Rationale |
|---|---|---|
| SECURITY.md (active code audit) | ✅ **Build — first** | Strongest demo moment; fits existing scanner architecture; deterministic Python = reliable on camera |
| PROMPTS.md (prompt library) | ✅ Build — second | Differentiating, ~½ day; must use detected specifics (real test cmd, real dirs), not generic stack prompts |
| MCP recommendations in CLAUDE.md | ✅ Build — cheap version | Static curated catalogue (stack → servers) in Python, Planner adapts it. NOT live research, NOT a KB — hours instead of days, reliable in demo |
| Foundry IQ knowledge bases | ⚠️ Build **one** (security patterns), timeboxed ½ day | 3 KBs in 2.5 days is unrealistic. One KB + Tavily fallback is enough to demo "agent chooses knowledge source and why". If SDK fights us → cut, keep Tavily |
| AI_DECISIONS.md template | ❌ Cut | Static template, no reasoning shown, looks like filler to judges |

## Key design decisions

### D1 — Security scanner is report-only
The scanner **detects and reports** code-level issues; it never auto-fixes source
code. Auto-fixing user code is a scope trap and conflicts with the safety rules
(backup/confirmation complexity explodes). Output is a generated `SECURITY.md`
written through the existing Executor confirmation gate like any other file.

### D2 — High-signal checks only, false positives are worse than misses
A false positive on camera kills the demo. Initial check set (narrow by design):

1. **Hardcoded secrets** — known key formats (`sk-`, `AKIA`, `ghp_`, `xox.-`,
   `AIza`) + generic `api_key/secret/token/password = "<long literal>"`
2. **`eval()` / `exec()`** on Python source (+ `eval(` in JS/TS)
3. **SQL built with f-strings / concatenation** — f-string containing
   SELECT/INSERT/UPDATE/DELETE with interpolation
4. **`verify=False`** in HTTP calls
5. **`debug=True`** in app entrypoints
6. **`shell=True`** in subprocess calls (medium severity — legitimate uses exist)

Guards: placeholder denylist (`example`, `changeme`, `your-`, `xxx`, `<`, `$`,
`dummy`, `test`), skip vendored/build dirs (`node_modules`, `.venv`, `dist`,
`__pycache__`, `.git`), only scan source extensions (`.py .js .ts .tsx .jsx`),
cap file size and finding count.

### D3 — New module `src/tools/security_scan.py`, not an edit to fs_tools.py
`fs_tools.py` is 500 lines and battle-tested; M2 memory says don't churn it.
Code-level scanning is a distinct concern from config detection (`detection.py`)
and file I/O (`fs_tools.py`). New module, same conventions (pure functions,
type hints, pathlib, returns plain dicts).

### D4 — Findings live in a new scan_result key: `code_security_findings`
The existing `security_issues` key means *config-level* issues (.env not
gitignored etc.) and the Planner/Verifier already key off it. Code-level findings
are a separate list so nothing downstream breaks. Scanner agent gets one new
tool: `scan_security_patterns(project_path)`.

### D5 — Dedicated seeded fixture: `tests/fixtures/vulnerable-project/`
Existing fixtures stay in their known before-state (M2 testing depends on it).
New fixture is a small FastAPI-ish project with 4–5 planted vulnerabilities —
one per check category — so the demo reliably shows the catch.

### D7 — In-string guard for code-pattern checks
First false-positive sweep (scanning our own `src/`) flagged 12 phantom findings:
every one was *prose about a pattern* inside a string literal or docstring
(e.g. the tool description "disables verify=False"). Fix: code-pattern checks
(eval/exec, verify=False, debug=True, shell=True) skip matches inside string
literals (`_in_string_at`, quote-state walk) and lines inside Python
triple-quoted strings (parity state machine). Secret and SQL checks are exempt —
their matches legitimately live inside strings. After the fix: 0 findings on
`src/` (14 files), 7/7 planted findings on the vulnerable fixture.

### D8 — Foundry-mode SECURITY.md: model formats, scanner detects
In Foundry mode the Planner model generates file content, but findings are
deterministic facts. PLANNER_INSTRUCTIONS require every finding rendered
verbatim (file, line, snippet, recommendation) — the model adds structure and
prioritisation, never re-detection. Same rule on the Scanner side: "copy
code_security_findings verbatim from the tool output."

### D9 — Deterministic security backstop in the Foundry Planner
Observed: an o4-mini planner run with 7 code findings and an exposed `.env`
produced neither SECURITY.md nor `.gitignore` — the model stochastically drops
actions as instruction load grows. Security actions are non-negotiable
(CLAUDE.md safety rules), so `_ensure_security_actions()` post-checks every
parsed Foundry plan and injects the locally-generated `.gitignore` /
SECURITY.md actions if missing, re-ranking them to the top and flagging the
restoration in plan_summary. The model plans; Python guarantees the floor.

### D10 — Knowledge base = Agents-API vector store + file_search, not portal IQ
The installed SDK (azure-ai-agents 1.2.0b6) ships FileSearchTool with
vector-store operations — fully scriptable provisioning, no Azure portal or
AI Search resource needed. `scripts/setup_kb.py` uploads `kb/security-patterns/`
(4 authored, OWASP-mapped docs) and writes FOUNDRY_KB_VECTOR_STORE_ID to .env.
The Researcher attaches file_search alongside Tavily when the env var is set
and must justify per-query source choice (`knowledge_sources_used` output
field) — that source-selection reasoning is the Best Reasoning Agent demo
moment. Gotcha: upload purpose is `"assistants"`, not `"agents"`.

### D6 — Claims hygiene in submission materials
The M3 proposal doc leans on unverifiable stats ("45% of AI code", "nobody else
ships this"). Per prior reviewer feedback (withdrawn 340k→140k token claim),
the paper only states what the code demonstrably does, with explicit sourcing
for any external stat.

## Build order

1. **Security scanner** — `security_scan.py` + scanner agent wiring + vulnerable
   fixture + SECURITY.md generation in Planner ← _in progress 2026-06-10_
2. PROMPTS.md action in Planner
3. MCP static catalogue → CLAUDE.md section
4. One Foundry IQ KB (timeboxed), Researcher chooses KB vs Tavily
5. Full Foundry-mode run on all fixtures, then README / video / submission

## Log

- **2026-06-10** — Pulled main (local was stale at `a5e1fec`, pre-M2-rewrite);
  recreated `M3-Expanded-Functionality` branch from merged tip `b58600b`.
  Fixed main's upstream (pointed at deleted `M0-Project-Setup`).
- **2026-06-10** — Decision record created; starting security scanner.
- **2026-06-10** — Security scanner built and tested:
  - `src/tools/security_scan.py` — 7 check categories, redaction, placeholder
    denylist, in-string + docstring guards (D7)
  - Scanner agent: new `scan_security_patterns` tool (both modes), findings in
    `code_security_findings`, priority bump on high-severity findings
  - Planner agent: `_generate_security_md()` local generator + Foundry
    instructions for verbatim rendering (D8)
  - Fixture `tests/fixtures/vulnerable-project/` — 7 planted vulns (one per
    check category) + exposed `.env` for the config-level check
  - Tested: 7/7 planted findings caught; 0 false positives on own `src/`;
    full local pipeline on a temp copy wrote `.gitignore` + `SECURITY.md` +
    `CLAUDE.md`, verifier 3/3; no regressions on the three existing fixtures.
- **2026-06-10 (later)** — Foundry mode tested:
  - Scan-only on vulnerable-project: ✅ o4-mini called scan_security_patterns,
    all 7 findings copied verbatim into code_security_findings, priority high
  - Full pipeline on temp copy: ✅ 5 files written, Verifier 5/5, SECURITY.md
    rendered all 7 findings verbatim (D8 held — nothing invented/dropped)
  - Observation: Foundry SECURITY.md format flatter than local generator
    (Type:/Severity: lists, no severity grouping). Added explicit format spec
    to PLANNER_INSTRUCTIONS — **re-run pending** to validate the tweak.
  - Observation: model also wrote .cursorrules/.cursorignore though Cursor not
    detected — pre-existing M2 behaviour, not an M3 regression. Left as-is.
  - Nothing committed yet — all M3 work (scanner, fixture, docs) is uncommitted
    on M3-Expanded-Functionality.
- **2026-06-11** — Security scanner committed (`29d5c4c`). Foundry re-run with
  the format spec: ✅ SECURITY.md now severity-grouped with code fences,
  findings verbatim. Verifier reported 1/5 this run (vs 5/5 prior) — known
  strict/qualitative variance from M2 (PROGRESS.md), amplified here because
  vulnerable-project has no detectable test/lint commands so CLAUDE.md gets
  placeholders. **Demo note:** demo the security story on vulnerable-project
  and the config story on a fixture with real conventions, or add a
  pyproject.toml to vulnerable-project.
- **2026-06-11** — PROMPTS.md built (M3 item #2):
  - `_generate_prompts_md()` in planner: 5 general + stack-specific prompts,
    each embedding the real test command, key directories, and frameworks
    (FastAPI fixture and TS/React fixture produce different libraries — verified)
  - Scanner emits `has_prompts_md` (deterministic check via
    read_existing_context_file in both modes) so the action only fires when
    the file is missing — idempotency verified on a second pipeline run
  - PLANNER_INSTRUCTIONS: format spec + "must reference actual scan data" rule
  - Local pipeline: 4/4 verified incl. PROMPTS.md. Foundry-mode run pending
    (will validate together with the MCP catalogue change).
- **2026-06-11** — MCP catalogue built (M3 item #3):
  - `src/tools/mcp_catalog.py` — static curated catalogue (GitHub, Postgres,
    Playwright, Fetch), stack-matched, max 3 recommendations. Install commands
    human-verified — corrected Fetch to `uvx mcp-server-fetch` (the reference
    fetch server is Python; there is no @modelcontextprotocol/server-fetch npm
    package). Deliberately not live-researched (demo reliability).
  - Local: `_generate_claude_md` adds "## Recommended MCP servers". Foundry:
    recommendations computed in Python, passed in the task payload, model must
    copy names/commands VERBATIM (same fabrication-control principle as D8).
  - vulnerable-project hardened for demo: added pyproject.toml (+ tiny test)
    so conventions resolve (pytest, ruff check ., app/ + tests/) — CLAUDE.md
    and PROMPTS.md now embed real commands instead of fallbacks; still exactly
    7 scan findings (no FPs from the new files).
- **2026-06-11** — Foundry run validated PROMPTS.md + MCP recs (verbatim
  commands ✅, stack-specific prompts ✅) **but dropped .gitignore and
  SECURITY.md** → D9 backstop built and unit-tested (injects when missing,
  no-ops when present).
- **2026-06-11** — Foundry IQ KB built (M3 item #4, D10): 4 KB docs authored,
  vector store `vs_qXyitIj7TGKhtmSjodZj2yMw` provisioned, Researcher wired
  with file_search + source-choice instructions, Tavily fallback when env var
  absent. All-up --verbose validation run in progress.
- **2026-06-11** — All-up validation run #1 results:
  - ✅ KB retrieval is the demo moment: Researcher cited KB docs inline
    (`【4:2†fastapi-python.md】`), mapped every scan finding to OWASP, and
    emitted knowledge_sources_used with per-source justification
  - ✅ Planner produced ALL 6 actions (SECURITY.md format perfect, CLAUDE.md
    with KB-informed DO NOTs + verbatim MCP commands, PROMPTS.md present)
  - ❌ Executor hit the silent `max_iterations=120` polling cap mid-plan:
    5 of 6 files written, no final JSON → "0 file(s) written" reported,
    Verifier saw nothing. Root cause of the earlier "model under-reported"
    symptom too. Fixes: (a) write_file handler keeps a ground-truth ledger and
    executed/errors/summary are built from it, never from the model's
    self-report; (b) executor + verifier polling budget raised to 400;
    (c) run_agent_with_tools now warns loudly when the cap is hit
  - Backstop extended to PROMPTS.md (planner had dropped it in the previous
    run — stochastic across runs). Validation run #2 in progress.
- **2026-06-11** — Run #2 died on `rate_limit_exceeded` (o4-mini deployment,
  too many back-to-back pipeline runs). Demo-day failure mode → added retry
  with backoff to run_agent_with_tools (new run on the same thread, 30/60/90s,
  raises after 3 attempts). Local pipeline regression after executor ledger
  change: ✅ 4/4. Run #3 in progress.
  **Demo-day note: space pipeline runs a few minutes apart, or request a
  higher TPM quota for the o4-mini deployment before recording.**
- **2026-06-11** — Validation run #3: ✅ all 6 files written AND correctly
  reported (ledger), Verifier 4/6 passed with the 2 "failures" being
  qualitative suggestions surfaced as recommendations (good demo content,
  left as-is). The new cap warning caught the Planner still in_progress at
  120 iterations (it generates 6 files of content in one JSON) — polling
  budget raised to 400 across all five agents. **M3 build scope complete.**
  Remaining: cross-fixture Foundry pass, README, demo video, registration,
  submission.

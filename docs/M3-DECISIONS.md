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

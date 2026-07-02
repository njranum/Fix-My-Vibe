# Fix My Vibe — Demo Runbook

A repeatable ~3-4 minute demo, run **entirely inside Claude Code**. Claude orchestrates
Fix My Vibe's Azure agents as MCP tools: it diagnoses a vibe-coded project, plans fixes
grounded in an Azure AI Search knowledge base (**Foundry IQ**), and applies them with
your confirmation. (Most of the time is the one cloud plan phase; `apply_fixes` reuses
the plan `propose_fixes` computed, so it doesn't re-run it.)

The `/fix-my-vibe` command resets a fresh copy of the demo project every time, so you can
rehearse and present repeatedly from the same clean state.

---

## 0. One-time setup

- Use the venv interpreter (the #1 thing that breaks the demo — the wrong Python silently
  drops to local mode with no Foundry IQ):
  ```bash
  source .venv/bin/activate
  ```
- Make sure `.env` (repo root) has `FOUNDRY_PROJECT_ENDPOINT` and `AZURE_SEARCH_ENDPOINT`.
- The `fix-my-vibe` MCP server is declared in `.mcp.json`. The first time you launch
  Claude Code in this repo it asks you to approve the server — accept. Confirm with `/mcp`.

## 1. Before you present (every time)

```bash
bash demo/preflight.sh      # checks deps, Foundry endpoint, KB, fixture (no cloud calls)
```
Then do one **rehearsal run** of the actual demo (below) — it's the only real proof that
Azure auth and the KB are responding. If it ends with all checks verified, you're good.

> Optional: `bash demo/run.sh --yes` is a CLI smoke-test of the same backend if you want
> to confirm the cloud pipeline outside Claude Code. It is **not** the demo — just a check.

## 2. The story (what you say)

> "This is a small Flask shop API someone vibe-coded fast. No AI tool config, secrets
> sitting in a committed `.env`, and a couple of security bugs an AI assistant would
> happily introduce. Fix My Vibe is a team of Azure agents that diagnoses this, looks up
> current best practice in a knowledge base, and fixes it — with my approval first."

The project has **5 detected problems**:
1. No `CLAUDE.md` (Claude Code is in use)
2. `.env` with secrets, not gitignored
3. Hardcoded Stripe key (`app.py`)
4. SQL injection (`app.py`)
5. Flask debug mode left on (`app.py`)

## 3. The demo

```bash
claude          # launch Claude Code in the repo root
```
In the session, type:
```
/fix-my-vibe
```
Claude resets a fresh demo copy, then calls the three MCP tools in turn and narrates each.
Point it at any project instead: `/fix-my-vibe /path/to/some/project`.

### What happens, and what to say

| Step (Claude calls...) | What you'll see | Say this |
|---|---|---|
| `scan_project` | `"mode": "foundry"`, Claude Code detected, the 5 problems | "The Scanner agent diagnosed it on Foundry — five issues, including two high-severity code bugs." |
| `propose_fixes` | a ranked plan; the code fixes carry `kb_citations` like *OWASP - Secrets Management Cheat Sheet* | "This is Foundry IQ — the fixes are grounded in our Azure AI Search KB, and each one cites the source it came from, not the model's memory." |
| `apply_fixes` | a **confirmation prompt — one checkbox per fix** | "Nothing is written without approval." — tick the fixes and confirm. |
| (result) | files written with `.bak` backups, then `N/N files verified` | "A Verifier agent confirmed every change landed correctly." |

> The KB lookups happen inside the MCP server, so the raw `[KB] ...` log lines are **not**
> shown in Claude Code (they're server-side). Your visible proof of Foundry IQ is the
> **citations attached to the proposed fixes** — point at those.

## 4. Optional aside — the performance story (CLI, not part of the demo)

If you want to tell the "we made it fast" story, run this in a terminal (not Claude Code):
```bash
bash demo/run.sh --yes --trace
```
It prints a per-phase timing table. Talking point: "This used to take ~9 minutes — we
instrumented it, found agents doing deterministic work through an LLM, and moved that to
Python. Now ~1.5 minutes, with the model only where there's real reasoning."

## 5. Reset / repeat

Nothing to do — `/fix-my-vibe` starts from a fresh copy each time. Inspect results in
`.fmv-run/shop-api/` after a run (the next run overwrites it). Clean up with
`rm -rf .fmv-run`.

---

## If something goes wrong

- **`/mcp` shows `fix-my-vibe` failed** → the relative interpreter in `.mcp.json`
  (`.venv/bin/python3`) didn't resolve. Launch `claude` from the repo root, or re-register
  with absolute paths:
  `claude mcp add fix-my-vibe -- /full/path/.venv/bin/python3 /full/path/src/mcp_server.py`
- **`scan_project` returns `"mode": "local"`** → the server didn't load `.env`, so there's
  no Foundry IQ. Confirm `.env` is in the repo root (the server loads it on startup).
- **`apply_fixes` wrote nothing** → you left every checkbox unticked, or declined. Just
  re-run `/fix-my-vibe` (the copy is reset for you). Confirmed working in Claude Code.
- **A transient Azure error** → the pipeline retries automatically; if it still fails,
  re-run `/fix-my-vibe`.
- **Feels slow (>2.5 min)** → Azure latency varies. Your rehearsal run is the insurance;
  keep its output handy to talk over.

## Known quirks (so they don't surprise you)

- The number of applied fixes varies slightly between runs: the KB-grounded remediator is
  an LLM, so sometimes it fixes the SQL injection in-place and sometimes it only documents
  it in `SECURITY.md`. Both are correct.
- `apply_fixes` makes `.bak` backups of overwritten files but doesn't have an in-tool undo;
  that's another reason the demo runs on a throwaway copy.

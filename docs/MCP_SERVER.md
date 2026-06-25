# Fix My Vibe — MCP Server

Run Fix My Vibe as an MCP server so an agent (e.g. Claude Code) can call it as tools
instead of shelling out to the `fix-my-vibe` CLI. The CLI remains the fallback; both
front-ends call the same orchestrator phase functions (`run_plan_phase` /
`run_apply_phase`), so behavior is identical.

Server entry point: `src/mcp_server.py` (FastMCP, stdio transport).

## Tools

| Tool | Writes? | Purpose |
|------|---------|---------|
| `scan_project(project_path, mode="auto")` | No | Read-only diagnosis: detected tools, stack, security findings, priority. |
| `propose_fixes(project_path, mode="auto")` | No | Full scan → research → plan. Returns ranked actions with complete file content (dry run / review). |
| `apply_fixes(project_path, ctx, mode="auto")` | Yes | Plans, then asks via an **elicitation** prompt (one checkbox per fix) which to apply, writes only the selected files (with `.bak` backups), and verifies them. |

`mode`: `"auto"` (default — Foundry if `FOUNDRY_PROJECT_ENDPOINT` is set + `az login`,
else local pure-Python reasoning), `"local"`, or `"foundry"`.

## Setup decisions (and why)

- **Transport: stdio.** The server is launched on demand by the client and spoken to
  over stdin/stdout — no long-running service, no ports. Matches local single-machine use.
- **Registration scope: `local`** (private to this project in `~/.claude.json`). Chosen
  to (a) match the existing `azure-mcp` server, also registered project-local, and
  (b) avoid committing a machine-specific venv path into a repo-tracked `.mcp.json`.
  Switch to `project` scope later if the server should be shared via the repo.
- **Confirmation = elicitation, fail-safe.** `apply_fixes` writes only after the user
  ticks fixes in the elicitation prompt. If the client doesn't advertise elicitation
  capability, it writes nothing and returns `needs_review` (honors the non-negotiable
  "never write without explicit confirmation" rule). `propose_fixes` is the
  no-elicitation review path.

## Registering with Claude Code

```bash
claude mcp add fix-my-vibe -- \
  /Users/snoopy/Dev/hackathon/.venv/bin/python3 \
  /Users/snoopy/Dev/hackathon/src/mcp_server.py
```

Verify:
```bash
claude mcp list                 # fix-my-vibe should show ✔ Connected
claude mcp get fix-my-vibe      # shows scope, command, status
```

Remove:
```bash
claude mcp remove fix-my-vibe -s local
```

Prerequisites: `mcp>=1.12` installed in the venv (`.venv/bin/python3 -m pip install "mcp>=1.12"`).

## How to test it

### 1. Programmatic e2e (no live client needed)

The full `apply_fixes` path (subset selection, fail-safe, decline, backup-on-overwrite)
is exercised with the elicitation step mocked — per the project rule to mock confirmation
in tests. Run against a temp copy so the fixture is never clobbered:

```bash
cp -r tests/fixtures/vulnerable-project /tmp/fmv-demo
PYTHONPATH=. .venv/bin/python3 - <<'PY'
import asyncio, src.mcp_server as m
res = m.propose_fixes("/tmp/fmv-demo", mode="local")   # no writes
print(res["plan_result"]["plan_summary"])
PY
```

### 2. Live interactive test in Claude Code

Newly registered servers load in a **fresh session**, so start a new Claude Code session
in this project, then:

1. Make a throwaway copy (never run on the real fixture):
   ```bash
   cp -r tests/fixtures/vulnerable-project /tmp/fmv-demo
   ```
2. Ask the agent: **"Use fix-my-vibe to scan /tmp/fmv-demo"** → confirms `scan_project`
   returns findings (no files written).
3. Ask: **"Use fix-my-vibe to propose fixes for /tmp/fmv-demo"** → `propose_fixes` returns
   the ranked plan with full content (still no files written).
4. Ask: **"Use fix-my-vibe to apply fixes to /tmp/fmv-demo"** → `apply_fixes` triggers the
   **elicitation checkbox prompt**. Tick a subset (e.g. just `.gitignore` and `SECURITY.md`).
5. Verify only the selected files were written and backups exist where applicable:
   ```bash
   ls -la /tmp/fmv-demo            # only chosen files present
   ls -la /tmp/fmv-demo/*.bak      # .bak backups for any overwritten file
   ```
6. Clean up: `rm -rf /tmp/fmv-demo`

Expected: declining the prompt or ticking nothing writes zero files; a client without
elicitation support returns `needs_review` and writes nothing.

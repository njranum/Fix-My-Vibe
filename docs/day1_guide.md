# Fix My Vibe — Day 1 Guide

*Microsoft AI Skills Fest, Agents League — Day 1: Tue 10 Jun 2026*  
*Goal: Scanner Agent + CLI skeleton working end-to-end*

---

## What Day 1 Delivers

`python3 src/cli.py <project_path> --local` produces a structured JSON diagnosis in the terminal.

By end of day:
- Scanner agent with three-layer detection is running
- CLI responds to `fix-my-vibe <path>`
- Three fixture projects produce correct output
- Foundry connection tested (if Azure is provisioned)

---

## Current State

These files are already built from project setup:

| File | Purpose |
|------|---------|
| `src/tools/fs_tools.py` | Raw filesystem tools (scan_directory, write_file, verify_file, etc.) |
| `src/tools/detection.py` | Three-layer detection helpers |
| `src/agents/scanner.py` | Scanner agent with Foundry + local modes |
| `src/cli.py` | CLI entrypoint (`--local`, `--verbose`, `--json`, `--yes`, `--scan-only`) |
| `src/foundry_utils.py` | Foundry SDK wrappers |
| `tests/fixtures/bare-project/` | FastAPI + .env, no AI tools configured |
| `tests/fixtures/cursor-project/` | Cursor .cursorrules present, .env exposed, missing .cursorignore |
| `tests/fixtures/node-typescript/` | React/TS + Vite, Copilot in VS Code extensions, .env.local exposed |

---

## 1.1 — Test the local pipeline

Run the Scanner in local mode (no Azure needed):

```bash
python3 src/cli.py tests/fixtures/bare-project --local --scan-only
```

Expected output:
- `detected_stack`: `["python", "fastapi"]`
- `security_issues`: `.env` exposed (not in `.gitignore`)
- `detected_tools`: whatever is installed on your machine (Layer 2 checks system PATH — `claude`, `cursor`, `aider` — not just the project directory)
- `missing_configs`: those same tools listed as missing, because the fixture has no config files for them

> **Note:** The fixture has no AI tool config files (Layer 1) and no `.vscode/extensions.json` (Layer 3), so any tools detected come purely from your system PATH. This is correct — the scanner is telling you "you have X installed but this project has no config for it."

To run the full pipeline:

```bash
python3 src/cli.py tests/fixtures/bare-project --local --yes
```

`--yes` skips the confirmation prompt. Expected: config files written + verified.

---

## 1.2 — Scanner agent tools (reference)

The Scanner calls these five tools in sequence:

1. `scan_directory(project_path)` — file signatures, stack detection, security scan (Layer 1)
2. `check_path_tools()` — `shutil.which()` check for installed CLIs (Layer 2)
3. `check_vscode_extensions(project_path)` — reads `.vscode/extensions.json` (Layer 3)
4. `read_existing_context_file(project_path, filename)` — audits existing CLAUDE.md / .cursorrules
5. `infer_project_conventions(project_path)` — detects build/test commands, naming style, README summary

All defined in `src/tools/fs_tools.py`.

**Key detection improvements over the initial scaffold:**

- Hidden config dirs (`.github/`, `.cursor/`, `.vscode/`) are now walked (previously skipped)
- FastAPI detection is content-based (`requirements.txt` contains `fastapi`) — not just `main.py` filename
- Gitignore pattern matching handles globs: `.env.*` covers `.env.local`
- `infer_project_conventions` reads Makefile, pytest.ini, setup.cfg, mypy.ini, pre-commit config
- Existing `.gitignore` gets UPDATED (not recreated) if missing env patterns

---

## 1.3 — Fixture projects

Three fixtures are built. Each demonstrates a different detection scenario:

### `tests/fixtures/bare-project/`
Python/FastAPI + `.env` exposed. No AI tool config files, no `.gitignore`.
- Expected: security issue (exposed_env), `detected_tools` from PATH only, missing CLAUDE.md

### `tests/fixtures/cursor-project/`
Python/FastAPI + `.cursorrules` present but weak quality + `.env` exposed + no `.cursorignore`.
- Expected: `cursor` detected (Layer 1 config), 2 security issues (exposed_env + missing_cursorignore), `.cursorrules` quality audit shows "Very short"

### `tests/fixtures/node-typescript/`
React/TypeScript/Vite + Copilot in `.vscode/extensions.json` + `.env.local` exposed.
- Expected: `copilot` detected (Layer 3 VS Code extensions), `.env.local` security issue, test/build/lint commands from `package.json`

Run all three:
```bash
python3 src/cli.py tests/fixtures/bare-project --local --scan-only
python3 src/cli.py tests/fixtures/cursor-project --local --scan-only
python3 src/cli.py tests/fixtures/node-typescript --local --scan-only
```

---

## 1.4 — Connect to Azure Foundry (if provisioned)

Check that `.env` has all three values:
```
FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/fix-my-vibe
FOUNDRY_MODEL_DEPLOYMENT_NAME=Phi-4-reasoning
TAVILY_API_KEY=tvly-...
```

> **Architecture note:** Phi-4-reasoning does NOT support function calling in the Agents API.
> All five agents use the inference API (`client.inference.get_chat_completions_client()`) instead.
> Tool calls (scan_directory, write_file, etc.) run in Python — results are passed as context.
> Phi-4 handles only plain-text reasoning + priority/verdict classification.

Confirm credentials, then run:
```bash
az account show   # must show the right subscription

python3 src/cli.py tests/fixtures/bare-project --scan-only --verbose
```

With `FOUNDRY_PROJECT_ENDPOINT` set and no `--local`, `--scan-only` routes through Foundry.
The `--verbose` flag prints Phi-4-reasoning's full chain-of-thought as a `_reasoning_trace` block.
That is your proof the model is being invoked — if you see the reasoning trace, Foundry is working.

Pass `--local` to force local mode regardless of whether the endpoint is set:
```bash
python3 src/cli.py tests/fixtures/bare-project --scan-only --local
```

> **Why no agents in the portal?** All agents are deleted after each run by design.
> To see run history, go to your Foundry project → **Tracing**.

---

## 1.5 — End of Day 1 checklist

- [ ] `python3 src/cli.py tests/fixtures/bare-project --local --scan-only` prints valid output
- [ ] Scan result includes `detected_stack`, `security_issues`, `missing_configs`
- [ ] `python3 src/cli.py tests/fixtures/cursor-project --local --scan-only` shows 2 security issues
- [ ] `python3 src/cli.py tests/fixtures/node-typescript --local --scan-only` detects `copilot` via Layer 3
- [ ] `python3 src/cli.py tests/fixtures/bare-project --local --yes` writes files + verifies
- [ ] (If Azure provisioned) `--scan-only` without `--local` uses Foundry, reasoning trace visible with `--verbose`
- [ ] All changes committed and pushed

```bash
git add src/ tests/ docs/
git commit -m "feat: Scanner agent with 3-layer detection and CLI skeleton"
git push
```

---

## Reasoning Traces — demo feature

Phi-4-reasoning emits chain-of-thought as plain text (no `<think>` tags via the inference API).
The raw model output is captured as `_reasoning_trace` in each agent's result dict.
In `--verbose` mode the trace is printed inline, clearly separated from the structured output.

The Planner trace is the money-shot for the Best Reasoning Agent prize — it shows the model
reasoning step-by-step through security priority, missing config decisions, and risk assessment.

---

## Troubleshooting

**`DefaultAzureCredential` fails:** Run `az login`. Corporate tenant? Try `az login --tenant <tenant-id>`.

**Model not found:** Check exact deployment name in Foundry portal → Models + Endpoints.
Case-sensitive — `Phi-4-reasoning` not `phi-4-reasoning`. Update `FOUNDRY_MODEL_DEPLOYMENT_NAME` in `.env`.

**Phi-4 returns server_error immediately:** This is the known function-calling failure.
All agents are now on the inference API — `client.agents.*` is no longer used for tool calls.
If you see this error, check that you're not accidentally running old agent code.

**Tavily key not found:** Confirm `TAVILY_API_KEY=tvly-...` is in `.env` and `load_dotenv()` is called.
The Researcher gracefully falls back to static KB if Tavily is unavailable.

**JSON parse error from model:** `foundry_utils.parse_json_response()` has three extraction strategies.
If all fail, the result dict will contain `{"error": "Failed to parse JSON...", "raw": "..."}`.
For the scanner and planner, JSON comes from local Python — this error shouldn't appear.

---

*Day 1 of 4 — fix-my-vibe*  
*Next: Day 2 — Researcher (Tavily) + Planner agents with Foundry integration*

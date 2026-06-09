# Fix My Vibe

> Multi-agent AI coding tool setup diagnostics — Microsoft AI Skills Fest, Agents League

Fix My Vibe scans your project, detects which AI coding tools you're using (Claude Code, Cursor, Copilot, Windsurf, Aider), diagnoses missing or misconfigured setup, and generates the correct configuration files — tailored to your stack and conventions.

## Quick start

```bash
pip install -e .
fix-my-vibe /path/to/your/project
```

No Azure setup? Run in local mode:

```bash
fix-my-vibe /path/to/your/project --local
```

## Commands

```
fix-my-vibe <path>            Full scan + plan + fix
fix-my-vibe <path> --local    Run without Azure Foundry
fix-my-vibe <path> --scan-only  Scan only, no file writes
fix-my-vibe <path> --yes      Auto-confirm all actions
fix-my-vibe <path> --verbose  Show full agent output
fix-my-vibe <path> --json     Output JSON (for CI)
```

## Architecture

Five-role multi-agent system:

```
User → Orchestrator
         ├─→ Scanner Agent      (3-layer detection: config files, PATH, VS Code extensions)
         ├─→ Researcher Agent   (Bing Grounding — fetches current best practices)
         ├─→ Planner Agent      (reasoning — ranks issues, generates config content)
         ├─→ Executor Agent     (writes files — always asks confirmation first)
         └─→ Verifier Agent     (validates written files contain expected sections)
```

## Setup (Azure Foundry mode)

1. Copy `.env.example` to `.env`
2. Fill in your Azure AI Foundry endpoint and model deployment names
3. Run `python test_connection.py` to verify the connection
4. Run `fix-my-vibe <path>`

See `docs/day0_guide.md` for full Azure provisioning steps.

## Safety

- Never writes a file without explicit user confirmation
- Always creates backups before overwriting existing files
- Never traverses outside the target directory
- Path traversal blocked at the tool level

## Stack

- Python 3.11+
- Azure AI Foundry (Microsoft Agent Framework)
- Bing Grounding for web search
- No external databases — filesystem only

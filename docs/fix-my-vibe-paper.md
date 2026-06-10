# Fix My Vibe — Submission Paper

_Microsoft AI Skills Fest, Agents League_
_Team: Nicholas Ranum_
_Submitted: June 14, 2026_

---

## Abstract

Fix My Vibe is a multi-agent reasoning system that diagnoses and repairs AI coding tool
configuration in developer projects. Given any local project directory, it detects which
AI coding assistants are in use (Claude Code, Cursor, GitHub Copilot, Windsurf, Aider),
identifies missing or misconfigured setup, and generates correct, project-tailored
configuration files — with mandatory user confirmation before any file is written.

The system runs five genuinely agentic agents on Azure AI Foundry using o4-mini. Each
agent receives tool definitions, decides which tools to call based on what it finds, and
produces structured output that feeds the next agent. The Planner agent writes actual
configuration file content using the model's reasoning — not templates.

---

## Problem

AI coding tools (Claude Code, Cursor, GitHub Copilot, etc.) work significantly better when
given accurate project context via their respective configuration files (CLAUDE.md,
.cursorrules, copilot-instructions.md, etc.). Most developers either skip these files
entirely or write them once and never update them. The result: AI assistants that generate
incorrect code, use wrong commands, and miss project-specific conventions.

Additionally, many developers unknowingly expose secrets to their AI tools by failing to
configure ignore files (.cursorignore, .gitignore), creating security risks that are easy
to miss and hard to audit manually.

---

## Solution

Fix My Vibe takes a "reasoning before action" approach:

1. **Scanner** — o4-mini receives five tool definitions (filesystem scan, PATH check, VS Code
   extension inspection, existing config audit, convention inference) and decides which to call
   and in what order based on what it finds. Returns a complete picture of the project's AI
   tool setup.

2. **Researcher** — o4-mini receives the scan result and a `search_web` tool. It decides what
   queries to run for each detected tool and stack combination, fetching current best practices
   via Tavily. Returns per-tool research grounded in real documentation.

3. **Planner** — o4-mini reasons over the gap between current state and best practices,
   infers project conventions from the scan data, and generates the exact content for each
   config file — tailored to this specific project. The model writes CLAUDE.md, .cursorrules,
   .cursorignore, copilot-instructions.md. Not templates.

4. **Executor** — presents the plan to the user, requires explicit confirmation, then o4-mini
   calls `write_file` for each approved action, with automatic backup of existing files.

5. **Verifier** — o4-mini calls `verify_file` and `read_existing_context_file` on each written
   file, reasons about whether the content is actually useful (not just structurally present),
   and produces a quality verdict.

---

## Architecture

```
User → CLI → Orchestrator
                ├── Scanner    (tools: scan_directory, check_path_tools,
                │               check_vscode_extensions, read_existing_context_file,
                │               infer_project_conventions)
                │
                ├── Researcher (tools: search_web via Tavily)
                │
                ├── Planner    (no tools — pure reasoning + file content generation)
                │
                ├── [Confirmation gate — user approves plan before any writes]
                │
                ├── Executor   (tools: write_file with backup)
                │
                └── Verifier   (tools: verify_file, read_existing_context_file)
```

All Foundry agents follow the same pattern:
- Agent created with tool definitions
- o4-mini decides which tools to call
- Tools execute locally in Python, results returned to model
- Model reasons over results, produces structured JSON output
- Agent deleted after run

A `--local` flag exists for development and testing: same pipeline, Python fallbacks,
no Azure required. Not intended for end users.

---

## Genuine Agency

What makes this agentic rather than a deterministic pipeline:

- **Scanner** does not call all tools unconditionally. If it finds no `.vscode/` directory it
  skips the extension check. If it finds existing config files it calls `read_existing_context_file`
  to audit them. The model decides what's worth investigating.

- **Researcher** generates its own search queries. Given "cursor + fastapi", it decides to
  search for `.cursorignore` security practices specifically because the scanner flagged a
  missing cursorignore — not because a query template told it to.

- **Planner** writes file content from scratch. Given the scan result and research, it produces
  a CLAUDE.md that references the actual README summary, uses the exact test command found in
  the project, and adds DO NOT rules specific to the detected stack. Two different projects
  produce different CLAUDE.md files.

- **Verifier** makes qualitative judgments, not just structural checks. It can flag that a
  CLAUDE.md passes section checks but is still boilerplate, or that a .cursorrules is technically
  present but too short to be useful.

---

## Reasoning Traces

o4-mini produces chain-of-thought reasoning that is visible in `--verbose` mode. Unlike static
summaries, these traces reflect the model's actual decision-making during tool use:

```
[Scanner reasoning]
I can see requirements.txt with fastapi — this is a FastAPI project.
There's a .env file. I need to check if .gitignore covers it...
calling scan_directory... .gitignore exists but contains no .env pattern.
This is a high-severity security issue. Cursor is detected via .cursorrules
but I don't see .cursorignore — calling read_existing_context_file to audit
the cursorrules quality before deciding on missing_configs...
```

This directly addresses the Best Reasoning Agent judging criteria: the reasoning is real,
not narrated after the fact.

---

## Safety Design

- **Confirmation gate is mandatory** — no file is ever written without explicit user approval
- **Backup before overwrite** — existing files are backed up as `.bak` before replacement
- **Path traversal protection** — all write paths are validated to be inside the target directory
- **No GitHub URL ingestion** — filesystem input only; no remote code execution
- **Agent cleanup** — agents deleted after each run, no persistent state

---

## Installation and Setup

Fix My Vibe is a developer CLI tool — users provision their own Azure infrastructure once,
then run against any local project. No code is sent to a third-party service; the Azure
resources run in the user's own subscription.

```bash
# 1. Install
git clone https://github.com/njranum/Fix-My-Vibe
pip install -e .

# 2. Provision Azure resources (one-time, ~2 minutes)
az login
azd up   # creates resource group, AI Services, deploys o4-mini

# 3. Run against any project
fix-my-vibe ./my-project --verbose
```

`azd up` reads `infra/main.bicep` and provisions everything automatically — no manual
portal configuration. Required: Azure subscription with AI Services quota for o4-mini.

## Demo

```bash
# Standard run — shows plan, asks for confirmation before writing
fix-my-vibe ./my-project

# Show agent reasoning traces
fix-my-vibe ./my-project --verbose

# Scan only — no planning or file writes
fix-my-vibe ./my-project --scan-only

# Non-interactive (CI/scripting)
fix-my-vibe ./my-project --yes
```

---

## Prize Targets

- **Best Reasoning Agent** — o4-mini reasons over tool results before every decision; Planner
  generates file content via chain-of-thought reasoning, not templates; reasoning traces visible
  per agent in `--verbose` mode
- **Best Use of IQ Tools** — Tavily web search for current best practices, grounded in real
  documentation rather than static knowledge
- **Hack for Good** — improves developer security posture by detecting exposed `.env` files,
  missing `.cursorignore` configurations, and weak AI context files across the most popular
  AI coding tools

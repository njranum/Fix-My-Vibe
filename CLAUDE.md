# Fix My Vibe — CLAUDE.md

> **Hackathon:** Microsoft AI Skills Fest — Agents League  
> **Deadline:** June 14, 2026, 11:59 PM PT  
> **Target prize:** Best Reasoning Agent (secondary: Best Use of IQ Tools, Hack for Good, Top Student Award)

---

## Project Summary

Fix My Vibe is a Python-based multi-agent reasoning system built on Microsoft Foundry. It scans a local project directory, detects which AI coding tools are in use (Claude Code, Cursor, Copilot, Windsurf, Aider), diagnoses missing or misconfigured setup, and generates the correct configuration files for each tool — with user confirmation before any file writes.

The defining characteristic is **reasoning before action**: the agent determines what the user is working with, then produces tailored fixes — not templated output.

---

## Architecture

Five-role multi-agent system orchestrated via Microsoft Foundry:

```
User → Orchestrator
         ├─→ Scanner Agent      (filesystem read tools — detects stack + AI tools)
         ├─→ Researcher Agent   (Azure AI Search KB + Tavily — fetches current best practices)
         ├─→ Planner Agent      (pure reasoning — ranks issues, builds action plan)
         ├─→ Executor Agent     (filesystem write tools + confirmation gate)
         └─→ Verifier Agent     (filesystem read tools — validates outputs)
```

**Tool detection runs three layers in sequence:**
1. Config file signatures (`.cursorrules`, `CLAUDE.md`, `.github/copilot-instructions.md`, etc.)
2. `shutil.which()` PATH checks for installed CLIs
3. VS Code extension inspection (`.vscode/extensions.json`)
4. Single user prompt fallback if all three layers find nothing

**Key constraint:** File system input only — no GitHub URL ingestion.

---

## Stack

- **Language:** Python 3.11+
- **Agent platform:** Microsoft Foundry (Azure AI Foundry)
- **Orchestration:** Microsoft Agent Framework / Responses API
- **Knowledge base:** Azure AI Search (32 curated sources, OWASP/CWE/NIST + framework docs) — qualifies for Best Use of IQ Tools
- **Web search:** Tavily (fallback for novel/current queries)
- **CLI entrypoint:** `fix-my-vibe <path>`

---

## Repo Structure

```
fix-my-vibe/
├── CLAUDE.md                  ← this file
├── docs/                      ← planning docs (day 0 plan, brief, etc.)
├── kb/
│   ├── sources.json           ← 32 curated sources (OWASP, CWE, NIST, framework docs)
│   ├── kb_config.json         ← Azure AI Search index schema + embedding config
│   ├── ingest_security_kb.py  ← fetch → chunk → embed → upload to Azure AI Search
│   ├── security-patterns/     ← 4 authored OWASP-mapped docs (also indexed)
│   └── RESEARCHER_INTEGRATION.md ← integration reference
├── src/
│   ├── agents/
│   │   ├── scanner.py
│   │   ├── researcher.py      ← search_security_kb (KB-first) + search_web (Tavily fallback)
│   │   ├── planner.py
│   │   ├── executor.py
│   │   └── verifier.py
│   ├── tools/
│   │   ├── fs_tools.py        ← scan_directory, write_file, read_file
│   │   └── detection.py       ← three-layer tool detection logic
│   ├── orchestrator.py        ← main agent loop
│   └── cli.py                 ← CLI entrypoint
├── tests/
│   └── fixtures/              ← sample project dirs for testing
├── infra/                     ← azd provisioning (bicep/yaml)
├── azure.yaml                 ← azd config
├── requirements.txt
└── README.md
```

---

## Key Docs (reference frequently)

- **Agent brief:** `docs/agent-setup-brief.md`
- **Foundry tech notes:** `docs/foundry-tech-notes.md`
- **Build checklist:** `docs/build-checklist.md`
- **Submission paper:** `docs/fix-my-vibe-paper.md`
- **Foundry overview:** https://learn.microsoft.com/en-us/azure/foundry/agents/overview
- **Foundry Python samples:** https://github.com/microsoft-foundry/foundry-samples/tree/main/samples/python/hosted-agents
- **AISF submission repo:** https://github.com/microsoft/agentsleague/issues

---

## Build Priorities (in order)

1. Working Scanner Agent with three-layer detection
2. CLI entrypoint (`fix-my-vibe <path>`) that produces output
3. Planner Agent producing a ranked action plan
4. Executor Agent with confirmation gate before any file write
5. Researcher Agent with Azure AI Search KB + Tavily fallback
6. Verifier Agent validating outputs
7. Foundry reasoning traces visible and loggable
8. Demo video + README + GitHub repo public

---

## Safety Rules (non-negotiable)

- **Never write a file without explicit user confirmation** — always show the plan first
- **Never overwrite without backup** — executor must backup before writing
- **Never traverse outside the target directory** — validate all paths
- **Confirmation gate is not optional** — even in tests, mock the confirmation step

---

## Coding Conventions

- Use type hints throughout
- Each agent is a standalone module with a clear `run(input: dict) -> dict` interface
- Tool functions live in `src/tools/`, not inside agent files
- All file I/O goes through `fs_tools.py` — never raw `open()` calls in agent logic
- Tests go in `tests/` with fixture projects in `tests/fixtures/`
- Avoid hardcoding paths — always use `pathlib.Path`

---

## Known Issues / Open Questions

- Convention inference logic (how the Planner infers project conventions from file structure) was flagged as superficial in reviewer feedback — needs deeper implementation
- Token-count reduction metric (formerly claimed 340k → 140k) was unsupported — do not use until benchmarked on real fixture projects
- Foundry integration claims must be backed by actual SDK calls in code, not prose descriptions
- **Azure AI Search: `threat_categories` and `stack_applicable_to` not filterable** — these fields were created without `filterable=True` in the index schema, so OData filter expressions (`$filter`) fail at query time. Worked around by folding stack/threat hints into the search query text. To fix properly: add `filterable=True` to those `SimpleField` definitions in `ingest_security_kb.py`, delete and recreate the index, then re-run ingestion.

---

## Session Hygiene

At the end of each Claude Code session, write a `PROGRESS.md` in the project root summarising:
- What was built or changed
- Current state of each agent
- Next immediate task

This file is the context handoff for the next session.

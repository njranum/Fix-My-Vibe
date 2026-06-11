# Fix My Vibe — Build Checklist

_Microsoft AI Skills Fest, Agents League — Deadline June 14, 2026_

## Day 0 — Azure Provisioning ✅

- [x] `az login` + correct subscription confirmed
- [x] Foundry project created at ai.azure.com
- [x] `o4-mini` model deployed (serverless API)
- [x] Tavily API key obtained and in `.env`
- [x] GitHub repo created (public), code pushed
- [ ] Registered at aka.ms/AgentsLeague/AISF

## Day 1 — Core Pipeline ✅

- [x] `src/tools/fs_tools.py` — scan_directory, check_path_tools, check_vscode_extensions,
      infer_project_conventions, write_file, verify_file
- [x] `src/tools/detection.py` — three-layer merge
- [x] `src/agents/scanner.py` — local mode working
- [x] `src/agents/researcher.py` — local mode + Tavily
- [x] `src/agents/planner.py` — local mode with convention inference
- [x] `src/agents/executor.py` — confirmation gate + backup writes
- [x] `src/agents/verifier.py` — section checks + quality audit
- [x] `src/orchestrator.py` — full pipeline (local + Foundry routing)
- [x] `src/cli.py` — fix-my-vibe <path> [flags]
- [x] `tests/fixtures/bare-project/` — FastAPI + .env, no AI setup
- [x] `tests/fixtures/cursor-project/` — .cursorrules present, .env exposed
- [x] `tests/fixtures/node-typescript/` — Copilot via VS Code extensions
- [x] End-to-end local pipeline: all 3 fixtures pass

## Day 2 — Foundry Connection ✅ (partial)

- [x] Foundry connection working (`FOUNDRY_PROJECT_ENDPOINT` + `az login`)
- [x] Foundry pipeline routes correctly via CLI
- [x] Tavily search confirmed working (4 URLs from 2 searches)
- [x] Reasoning traces visible in `--verbose` mode
- [x] Fixtures restored to correct before-state (generated outputs removed from git)
- [ ] **Foundry pipeline is NOT genuinely agentic** — was using Phi-4-reasoning which
      does not support function calling. All tool execution was in Python, model only
      wrote summary paragraphs. This is the core issue addressed in Day 3.

## Azure AI Search KB Integration ✅

Branch: `azure-ai-search-kb`

- [x] `kb/sources.json` — 32 curated sources (18 security: OWASP, CWE, NIST, framework docs; 14 best practices: Anthropic, IDE tools)
- [x] `kb/kb_config.json` — Azure AI Search index schema (vector search + semantic ranking)
- [x] `kb/ingest_security_kb.py` — fetch → chunk → embed (Azure OpenAI) → upload pipeline
- [x] Index `fix-my-vibe-security-kb` live at `https://fixmyvibeiq.search.windows.net`
- [x] `src/agents/researcher.py` — replaced Foundry `FileSearchTool` with `search_security_kb` tool (Azure AI Search, KB-first) + `search_web` (Tavily, fallback)
- [x] End-to-end Foundry run confirmed: 4 KB queries hit Azure AI Search with results (OWASP, CWE, internal patterns)
- [x] `knowledge_sources_used` in researcher output distinguishes KB vs. web per query
- [x] Orchestrator label shows "Azure AI Search + Tavily" when `AZURE_SEARCH_ENDPOINT` is set

Required env vars: `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_KEY`, `AZURE_SEARCH_INDEX`, `AZURE_OPENAI_ENDPOINT` (ingest only), `AZURE_OPENAI_KEY` (ingest only)

## Day 3 — Genuine Agentic Rewrite ✅

Model: switched from Phi-4-reasoning → o4-mini (supports function calling via Agents API)

- [x] `src/agents/scanner.py` — `run_with_foundry()` rewritten: o4-mini calls tools agentically
- [x] `src/agents/researcher.py` — `run_with_foundry()` rewritten: model chooses search queries
- [x] `src/agents/planner.py` — `run_with_foundry()` rewritten: model generates file content
- [x] `src/agents/executor.py` — `run_with_foundry()` rewritten: model calls write_file
- [x] `src/agents/verifier.py` — `run_with_foundry()` rewritten: model calls verify_file
- [x] End-to-end Foundry pipeline tested on bare-project with o4-mini — 4 files written
- [x] Planner-generated CLAUDE.md is project-specific (references FastAPI, real conventions)
- [ ] End-to-end Foundry pipeline tested on cursor-project and node-typescript
- [ ] Reasoning traces show model's tool-use decisions clearly in --verbose mode

## Day 4 — Polish + Submission 🔲

- [ ] `pyproject.toml` — entry point so `pip install -e .` creates `fix-my-vibe` command
- [ ] `infra/main.bicep` — complete azd template: AI Services resource + o4-mini deployment
- [ ] `azure.yaml` — verify azd config points to completed bicep
- [ ] README.md — install + one-time setup (`azd up`) + demo instructions
- [ ] Demo video recorded (3-5 min, shows `--verbose` with real tool-use reasoning)
- [ ] `docs/fix-my-vibe-paper.md` complete and accurate
- [ ] Security review: path traversal protection tested
- [ ] Confirmation gate tested (reject → no files written)
- [ ] Backup/restore tested (overwrite scenario)
- [ ] GitHub repo public with clear submission notes
- [ ] Registered at aka.ms/AgentsLeague/AISF
- [ ] Submission at https://github.com/microsoft/agentsleague/issues

## Distribution Model

Fix My Vibe is a developer CLI tool — users bring their own Azure credentials. Setup is a
one-time operation per machine:

```bash
# 1. Clone and install
git clone https://github.com/njranum/Fix-My-Vibe
cd Fix-My-Vibe
pip install -e .

# 2. Provision Azure infrastructure (one time)
az login
azd up   # provisions resource group, AI Services, o4-mini deployment

# 3. Use from any project
fix-my-vibe /path/to/my-project --verbose
```

`azd up` reads `infra/main.bicep` and creates all required Azure resources automatically.
Users need an Azure subscription; no manual portal configuration required.

## Key Technical Constraints

- Use `.venv/bin/python3`, not `python3` (system Python is Homebrew-isolated, no packages)
- Always `az login` before Foundry runs
- Agents are deleted after each run — no persistent state
- `run()` = local fallback (no model), `run_with_foundry()` = agentic (o4-mini)
- Confirmation gate is mandatory and always local — never inside an agent

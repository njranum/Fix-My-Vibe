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

## Day 3 — Genuine Agentic Rewrite 🔲 (current)

Model: switched from Phi-4-reasoning → o4-mini (supports function calling via Agents API)

- [ ] `src/agents/scanner.py` — `run_with_foundry()` rewritten: o4-mini calls tools agentically
- [ ] `src/agents/researcher.py` — `run_with_foundry()` rewritten: model chooses search queries
- [ ] `src/agents/planner.py` — `run_with_foundry()` rewritten: model generates file content
- [ ] `src/agents/executor.py` — `run_with_foundry()` rewritten: model calls write_file
- [ ] `src/agents/verifier.py` — `run_with_foundry()` rewritten: model calls verify_file
- [ ] End-to-end Foundry pipeline tested on all 3 fixtures with o4-mini
- [ ] Reasoning traces show model's tool-use decisions, not post-hoc summaries
- [ ] Planner-generated CLAUDE.md is project-specific (not template output)

## Day 4 — Polish + Submission 🔲

- [ ] `docs/fix-my-vibe-paper.md` complete and accurate
- [ ] README.md complete with install + demo instructions
- [ ] Demo video recorded (3-5 min, shows `--verbose` with real tool-use reasoning)
- [ ] Security review: path traversal protection tested
- [ ] Confirmation gate tested (reject → no files written)
- [ ] Backup/restore tested (overwrite scenario)
- [ ] GitHub repo public with clear submission notes
- [ ] Submission at https://github.com/microsoft/agentsleague/issues

## Key Technical Constraints

- Use `.venv/bin/python3`, not `python3` (system Python is Homebrew-isolated, no packages)
- Always `az login` before Foundry runs
- Agents are deleted after each run — no persistent state
- `run()` = local fallback (no model), `run_with_foundry()` = agentic (o4-mini)
- Confirmation gate is mandatory and always local — never inside an agent

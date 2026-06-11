# Fix My Vibe — Progress

_Updated: 2026-06-11 (Day 5, branch `azure-ai-search-kb`) — Azure AI Search KB integrated_

---

## What Was Built (Azure AI Search KB branch)

Replaced the M3 Foundry `file_search` KB (4 authored Markdown files in a Foundry vector store)
with a production Azure AI Search index backed by 32 curated authoritative sources.

1. **Knowledge base sources** (`kb/sources.json`) — 32 sources in two categories:
   - 18 security: OWASP Top 10 2025 (A01–A09), CWE Top 25, NIST SSDF, NIST Crypto, OWASP
     Cheat Sheets (Secrets, Input Validation, Logging, Error Handling), FastAPI security,
     Django security, Express best practices, Python/Node.js crypto, CWE SQL injection + eval
   - 14 best practices: Anthropic docs (Build, Prompt Engineering, Cookbook, Vision, Tool Use,
     Context, Cost, Evaluation), Claude Code best practices, IDE integrations (Cursor, Copilot,
     Aider, Windsurf)
   - Includes `local://hackathon/kb/security-patterns/` — the 4 authored docs are also indexed

2. **Ingestion pipeline** (`kb/ingest_security_kb.py`) — fetch → HTML strip → smart chunk
   (~500 tokens, newline-aware overlap) → batch embed (Azure OpenAI text-embedding-3-small)
   → upload to Azure AI Search. Index `fix-my-vibe-security-kb` is live at
   `https://fixmyvibeiq.search.windows.net`.

3. **Researcher agent rewrite** (`src/agents/researcher.py`):
   - `search_security_kb` tool: queries Azure AI Search, supports optional `stack_filter` +
     `threat_filter` (folded into query text — fields not OData-filterable in current index)
   - `search_web` tool: Tavily, explicit fallback for novel/current queries
   - `FileSearchTool` / Foundry vector store dependency removed entirely
   - Instructions updated: model told to prefer KB for security, web for current docs
   - `knowledge_sources_used` in output records per-query source choice + rationale

4. **Orchestrator** (`src/orchestrator.py`) — KB label now reads "Azure AI Search + Tavily"
   when `AZURE_SEARCH_ENDPOINT` is set.

5. **Dependencies** (`requirements.txt`) — added `azure-search-documents>=11.4.0`,
   `openai>=1.0.0`, `requests>=2.31.0`.

### End-to-end validation (vulnerable-project fixture, Foundry mode)
- Scanner: 7 findings (4 high, 3 medium) — all correct
- Researcher: 4 KB queries confirmed hitting Azure AI Search with 5 results each:
  - `claude code config file security best practices` (python, config)
  - `hardcoded secret` (python, secrets)
  - `sql injection parameterized queries fastapi` (python, injection)
  - `verify=False requests python security pattern` (python, config)
  - 1 web search for current CLAUDE.md format docs
- Planner: 6 files planned (SECURITY.md, .gitignore, CLAUDE.md, .cursorrules, .cursorignore, PROMPTS.md)
- Executor: all 6 written
- Verifier: 4/6 pass (2 qualitative suggestions on .cursorrules + PROMPTS.md — by design)

---

## What Was Built (M3, branch `M3-Expanded-Functionality`) — merged to main

Full decision record with rationale: `docs/M3-DECISIONS.md` (D1–D10 + run log).

1. **Security scanner** (`src/tools/security_scan.py`) — 7 check categories
2. **PROMPTS.md** — stack-tailored prompt library
3. **MCP recommendations** — curated static catalogue (`src/tools/mcp_catalog.py`)
4. **Security knowledge base** — 4 authored OWASP-mapped docs (superseded by Azure AI Search KB)
5. **Robustness** — executor ground-truth write ledger, polling budgets, rate-limit retry,
   deterministic planner backstop

---

## Current State of Each Agent

| Agent | Local mode | Foundry mode |
|-------|-----------|--------------|
| Scanner | ✅ + code security scan | ✅ + scan_security_patterns tool |
| Researcher | ✅ static fallback | ✅ Azure AI Search KB-first + Tavily fallback |
| Planner | ✅ all 6 output files | ✅ + deterministic backstop |
| Executor | ✅ | ✅ ground-truth ledger, confirmation gate local |
| Verifier | ✅ | ✅ qualitative recommendations surfaced |

---

## Known Issues / Demo Notes

- `threat_categories` and `stack_applicable_to` are not marked filterable in the Azure AI
  Search index — OData filters fall back to folding terms into the query text. Works correctly
  for text search; to enable true OData filtering, re-index with `filterable=True` on those
  fields in the `SearchIndex` definition.
- **Rate limits**: space pipeline runs minutes apart or raise o4-mini TPM quota.
- Demo on `tests/fixtures/vulnerable-project` (security story + real conventions).
  Always run on a **temp copy**:
  `cp -r tests/fixtures/vulnerable-project /tmp/demo && .venv/bin/python3 src/cli.py /tmp/demo --verbose`

---

## Next Immediate Tasks

1. Merge `azure-ai-search-kb` branch → main
2. README.md for submission (install, azd, demo instructions)
3. Demo video 3–5 min: `--verbose` on vulnerable-project — show scan findings →
   KB-cited research → plan → confirmation gate → SECURITY.md/PROMPTS.md
4. `pyproject.toml` — entry point so `pip install -e .` creates `fix-my-vibe` command
5. `infra/main.bicep` — azd template: AI Services + o4-mini + Azure AI Search resources
6. Register at aka.ms/AgentsLeague/AISF; submit at
   https://github.com/microsoft/agentsleague/issues (deadline June 14, 11:59 PM PT)

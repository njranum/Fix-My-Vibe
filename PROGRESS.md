# Fix My Vibe — Progress

_Updated: 2026-06-24 (branch `azure-ai-search-kb`) — Azure MCP server added for direct index inspection_

---

## What Was Built (2026-06-24 session)

### Azure MCP Server configured

Added `@azure/mcp` (v3.0.0-beta.17) as a Claude Code MCP server for this project:

```
claude mcp add azure-mcp -- npx -y @azure/mcp@latest server start
```

Uses existing `az login` credentials (`nicjranum@gmail.com`). Gives Claude Code direct
access to Azure AI Search (list indexes, query KB, inspect schemas), Storage, Foundry,
and more — no separate API key needed. Config lives in `.claude.json` (project-local).

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

## Project Status

The Agents League hackathon (deadline June 14, 2026) has **concluded** — this is no
longer a submission sprint. Fix My Vibe continues as an ongoing personal project; the
goal now is a complete, stable, usable tool. Tasks below are framed for that, not for
a hackathon submission. (Separate prototype repo at `../fmv2` is retained, not retired.)

## Next Immediate Tasks

1. ✅ Merge `azure-ai-search-kb` branch → main (done — main now has the Azure AI Search KB)
2. README.md — install, configure, and run instructions for general use
3. `pyproject.toml` — entry point so `pip install -e .` creates the `fix-my-vibe` command
4. `infra/main.bicep` — azd template: AI Services + o4-mini + Azure AI Search resources
5. Address open quality items (convention inference depth, OData filterable fields — see
   CLAUDE.md Known Issues)

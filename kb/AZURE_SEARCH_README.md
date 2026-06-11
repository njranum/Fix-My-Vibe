# Fix My Vibe Security Knowledge Base

This directory contains the curated security knowledge base that powers the Researcher agent in Fix My Vibe. Instead of relying on web search for every query, the Researcher now queries an Azure AI Search index of authoritative security sources.

## Files

| File | Purpose |
|------|---------|
| `sources.json` | Curated list of 18 authoritative security sources (OWASP, CWE, NIST, etc.) |
| `kb_config.json` | Azure AI Search index configuration (schema, embedding model, filters) |
| `ingest_security_kb.py` | Ingestion script: fetches → chunks → embeds → uploads to Azure AI Search |
| `RESEARCHER_INTEGRATION.md` | How to integrate the KB with the Researcher agent |
| `README.md` | This file |

## Quick Start

### 1. Review Sources
```bash
cat sources.json | jq '.sources[] | {title, url, threat_categories}'
```

18 sources across:
- **OWASP Top 10 2025 + Cheat Sheets** (7 sources)
- **CWE + NIST** (3 sources)
- **Framework-specific** (Django, FastAPI, Express) (4 sources)
- **Language-specific** (Python, JavaScript/Node.js) (3 sources)
- **Fix My Vibe internal patterns** (1 source)

### 2. Run Ingestion
```bash
# Set Azure credentials
export AZURE_SEARCH_ENDPOINT="https://your-search.search.windows.net"
export AZURE_SEARCH_KEY="your-key"
export AZURE_OPENAI_ENDPOINT="https://your-openai.openai.azure.com"
export AZURE_OPENAI_KEY="your-key"

# Ingest (2-3 minutes)
python ingest_security_kb.py --sources kb/sources.json --config kb/kb_config.json
```

Output: Azure AI Search index `fix-my-vibe-security-kb` with ~325 chunks

### 3. Validate
```bash
python validate_kb_connection.py
```

### 4. Integrate with Researcher
See `RESEARCHER_INTEGRATION.md`

## Coverage

**7 Security Threat Categories (matched to scanner):**
- Hardcoded Secrets (OWASP A07)
- Eval/Exec Injection (OWASP A03)
- SQL Injection (OWASP A03)
- Weak Cryptography (OWASP A02)
- Debug Leaks / Logging (OWASP A09)
- Swallowed Exceptions (OWASP A09)
- Misconfiguration (OWASP A05)

**Best Practices Categories (ideal coding setups):**
- Claude Code best practices (CLAUDE.md, .claudeignore patterns)
- Prompt engineering (structuring context, examples, optimization)
- Context management (managing token budgets, caching, long sessions)
- Tool use patterns (function calling, orchestration)
- IDE setup (Claude Code, Cursor, GitHub Copilot, Windsurf, Aider)
- Cost optimization (batching, prompt caching, efficient usage)
- Project structure (organizing code for AI tools)

**Stack Coverage:**
- Python (Django, Flask)
- FastAPI
- JavaScript / Node.js (Express)
- React

## Architecture

```
Researcher Agent
       ↓
    Query KB: "SQL injection vulnerability in FastAPI"
       ↓
Azure AI Search Index
       ├─ Vector search (semantic matching)
       ├─ Metadata filters (stack, threat category)
       └─ Returns: [top 5 chunks with source URLs]
       ↓
Researcher includes citations in output
       ↓
"Based on [OWASP CWE-89], SQL injection occurs when..."
"See: https://cwe.mitre.org/data/definitions/89.html"
```

## Maintenance

**Refresh Strategy:** Quarterly
- Check OWASP/CWE for new versions (usually Jan)
- Verify all URLs still live
- Re-run `ingest_security_kb.py` to update index

**Fallback:** If KB has no results, Researcher falls back to Tavily web search

## Performance

- **Query latency:** 200-500ms (Azure AI Search)
- **Index size:** ~50-100 MB
- **Total chunks:** ~325
- **Cost:** Lower than pure web search (KB queries are batched, embeddings pre-computed)

## Files Generated at Runtime

- `kb_ingestion.log` — Detailed ingestion logs
- `VALIDATION_REPORT.md` — Report from validation script


# Researcher Agent Integration Guide

## How to Connect Fix My Vibe Researcher to Azure AI Search

This guide explains how to replace the Tavily web search backend in the Researcher agent with queries to the curated security knowledge base.

---

## Architecture Change

### Before (Current)
```
Researcher Agent → Tavily API → Web search → Results vary by day
                                         ↓
                                  Generic web results
                                  (not always authoritative)
```

### After (New)
```
Researcher Agent → Azure AI Search Index
                   ├─ Security threats (18 sources)
                   │  (hardcoded secrets, injection, crypto, logging, etc.)
                   │
                   └─ Best practices (14 sources)
                      (Claude Code setup, context management, IDE config)
                        ↓
                   (KB-first for known patterns + best practices)
                        ↓
                   (Fallback to Tavily if no KB results)
                        ↓
                   Grounded, complete responses with actionable guidance
```

Example queries the Researcher can now answer:
- "What's an SQL injection vulnerability?" → security KB returns threat definition + remediation
- "How should I structure my CLAUDE.md?" → best_practices KB returns patterns + examples
- "What's the best way to manage context in FastAPI?" → returns both security concerns + optimization patterns

---

## Step 1: Index the Knowledge Base

Before integrating, run the ingestion script:

```bash
cd /Users/snoopy/Dev/fmv2

# Ensure Azure credentials are set
export AZURE_SEARCH_ENDPOINT="https://your-search.search.windows.net"
export AZURE_SEARCH_KEY="your-search-key"
export AZURE_OPENAI_ENDPOINT="https://your-openai.openai.azure.com"
export AZURE_OPENAI_KEY="your-openai-key"

# Run ingestion (2-3 minutes)
python kb/ingest_security_kb.py --sources kb/sources.json --config kb/kb_config.json
```

**Output:** `fix-my-vibe-security-kb` index in Azure AI Search with ~325 chunks across 18 sources.

---

## Step 2: Update Researcher Agent Code

Find the Researcher agent in your hackathon codebase (typically in `src/agents/researcher_agent.py` or similar).

### Add Azure AI Search Import

```python
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
```

### Update the Tool Definition

**Before:**
```python
researcher_tools = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for information",
            # ...
        }
    }
]
```

**After:**
```python
researcher_tools = [
    {
        "type": "function",
        "function": {
            "name": "search_security_kb",
            "description": "Search the curated security knowledge base (Azure AI Search) for threat patterns, OWASP guidance, and stack-specific remediation",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language query"},
                    "stack_filter": {
                        "type": "string",
                        "description": "Optional: filter by stack (python, fastapi, javascript, react, nodejs, django, express, flask)"
                    },
                    "threat_filter": {
                        "type": "string",
                        "description": "Optional: filter by threat type (injection, crypto, logging, config, secrets, auth)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Fallback: Search the web for current information (used if KB doesn't answer the question)",
            # ... (keep existing Tavily config)
        }
    }
]
```

### Implement the Tool Handler

```python
def search_security_kb(query: str, stack_filter: Optional[str] = None, threat_filter: Optional[str] = None) -> str:
    """Query the Azure AI Search security knowledge base."""
    search_client = SearchClient(
        endpoint=os.getenv("AZURE_SEARCH_ENDPOINT"),
        index_name="fix-my-vibe-security-kb",
        credential=AzureKeyCredential(os.getenv("AZURE_SEARCH_KEY"))
    )
    
    # Build OData filter
    filters = []
    if stack_filter:
        filters.append(f"stack_applicable_to/any(s: s eq '{stack_filter}')")
    if threat_filter:
        filters.append(f"threat_categories/any(t: t eq '{threat_filter}')")
    
    odata_filter = " and ".join(filters) if filters else None
    
    # Perform search
    results = search_client.search(
        search_text=query,
        search_mode="any",
        select=["content", "source_url", "source_title", "threat_categories", "stack_applicable_to"],
        filter=odata_filter,
        top=5
    )
    
    # Format results
    formatted_results = []
    for result in results:
        formatted_results.append(
            f"**{result['source_title']}** ({result['source_url']})\n"
            f"Threats: {', '.join(result.get('threat_categories', []))}\n"
            f"{result['content'][:500]}..."
        )
    
    if formatted_results:
        return "\n\n".join(formatted_results)
    else:
        return None  # Signal to fall back to web search
```

### Integrate into Agent Loop

```python
def researcher_agent_loop(scan_result: dict, client: AzureOpenAI) -> dict:
    """Researcher agent with KB-first, web search fallback."""
    
    messages = [
        {
            "role": "user",
            "content": f"""You are a security research agent for Fix My Vibe.
            
Scanner found these issues in the project:
{json.dumps(scan_result, indent=2)}

For each issue, research best practices and remediation.

IMPORTANT: 
1. FIRST try the search_security_kb tool with the detected stack + threat type
2. If KB returns results, cite them directly
3. If KB has no results, fall back to search_web
4. Always cite sources (source_url)

Provide research output as JSON with:
- threat: name of threat
- stack: detected stack
- current_state: what the scanner found
- owasp_mapping: which OWASP category
- sources_used: KB or Web, and why
- remediation: recommended fix
- references: URLs
"""
        }
    ]
    
    # Call agent with tools
    response = client.beta.messages.create(
        model="claude-opus-4-turbo",
        max_tokens=2000,
        tools=researcher_tools,
        messages=messages
    )
    
    # Process tool calls
    while response.stop_reason == "tool_use":
        tool_use = next(b for b in response.content if b.type == "tool_use")
        tool_name = tool_use.name
        tool_input = tool_use.input
        
        if tool_name == "search_security_kb":
            # Try KB first
            kb_result = search_security_kb(
                query=tool_input["query"],
                stack_filter=tool_input.get("stack_filter"),
                threat_filter=tool_input.get("threat_filter")
            )
            
            if kb_result:
                tool_result = kb_result
                knowledge_source = "KB"
            else:
                # Fall back to web search
                tool_result = search_web(tool_input["query"])
                knowledge_source = "Web"
        
        elif tool_name == "search_web":
            tool_result = search_web(tool_input["query"])
            knowledge_source = "Web"
        
        # Continue conversation
        messages.append({"role": "assistant", "content": response.content})
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": f"[{knowledge_source}] {tool_result}"
            }]
        })
        
        response = client.beta.messages.create(
            model="claude-opus-4-turbo",
            max_tokens=2000,
            tools=researcher_tools,
            messages=messages
        )
    
    # Extract final output
    return {
        "research": response.content[0].text,
        "knowledge_sources_used": ["KB", "Web"]  # Track which sources were queried
    }
```

---

## Step 3: Validate the Connection

Run this validation script to ensure the KB is accessible:

```bash
python kb/validate_kb_connection.py
```

**Expected output:**
```
✅ Connected to Azure AI Search
✅ Index 'fix-my-vibe-security-kb' contains 325 chunks
✅ Sample query 1: "hardcoded secrets" → 5 results from OWASP + CWE sources
✅ Sample query 2: "SQL injection FastAPI" → 3 results filtered to FastAPI stack
✅ Sample query 3: "weak crypto Python" → 4 results for cryptography library guidance
```

---

## Step 4: Test End-to-End

Run the full pipeline on a test fixture:

```bash
fix-my-vibe tests/fixtures/vulnerable-project --verbose
```

In the output, look for:
- Scanner finds hardcoded secrets
- Researcher searches KB first: `[KB] OWASP A07 — Hardcoded Secrets`
- Researcher includes citations: `https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html`
- Planner generates SECURITY.md with KB-cited guidance

---

## Troubleshooting

### Issue: "Index not found"
**Cause:** Ingestion script hasn't run yet, or index name mismatch  
**Fix:** Run `python kb/ingest_security_kb.py` first

### Issue: "Connection refused / Unauthorized"
**Cause:** Missing or invalid Azure credentials  
**Fix:** Verify `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_KEY`, `AZURE_OPENAI_*` are set

### Issue: "KB returns no results for my query"
**Cause:** Query too specific, or stack filter too narrow  
**Fix:** Try without stack/threat filters first; then broaden

### Issue: "Researcher still using Tavily for all queries"
**Cause:** Tool handler not properly integrated, or KB tool not returning results  
**Fix:** Check logs; ensure `search_security_kb` is called before `search_web`

---

## Performance Notes

- **KB Query Latency:** ~200-500ms (Azure AI Search is fast)
- **Embedding Overhead:** Already paid at ingestion time (one-time 2-3 min)
- **Cost Savings:** 1 indexed KB query vs. 1 Tavily web search = lower API costs
- **Freshness:** KB updates quarterly; web search used as fallback for novel threats

---

## Next Steps

1. ✅ Run ingestion: `python kb/ingest_security_kb.py`
2. ✅ Update Researcher agent code (copy code snippets above)
3. ✅ Test with `validate_kb_connection.py`
4. ✅ Run end-to-end on test fixture
5. ✅ Deploy to Foundry with updated agent code

---

**Created:** 2026-06-11  
**Updated:** 2026-06-11  
**Status:** Ready to integrate

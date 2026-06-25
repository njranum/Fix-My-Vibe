"""
src/agents/researcher.py
Researcher agent: uses Azure AI Search KB (primary) + Tavily web search (fallback)
to fetch current best practices for each detected AI tool and the project's stack.
Produces structured research that feeds the Planner.
"""

import os
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.foundry_utils import (
    run_agent_with_tools,
    get_last_assistant_message_with_reasoning,
    parse_json_response,
    create_thread_and_send,
)


RESEARCHER_INSTRUCTIONS = """
You are the Researcher agent for Fix My Vibe. You gather current best practices
for AI coding tool configuration and security guidance for the detected stack.

You will be given a JSON scan result with detected_tools, detected_stack, and possibly
code_security_findings.

You have TWO knowledge sources — choose deliberately per question:
- search_security_kb (Azure AI Search): authoritative, OWASP-mapped reference on security
  patterns AI assistants introduce, per stack, plus AI tool config hygiene. PREFER this for
  anything security-related: explaining scan findings, security_notes, what to exclude from
  AI context files. Supports optional stack_filter and threat_filter parameters.
- search_web: live web search. Use for current tool documentation, config file formats,
  and community practices that change over time.
For every query you make, know WHY that source: curated = trusted and stable; web = current.

For each detected tool, gather:
1. The official documentation for its context/config file format
2. Current community best practices for that stack + tool combination
3. Any known security gotchas (e.g. what NOT to include in the context file)
4. Recommended sections for the config file given the detected stack
If the scan found code_security_findings, also query the knowledge base for the stack-specific
guidance on those finding types so the Planner can ground SECURITY.md recommendations.

Output a JSON object with this structure:
{
  "research": {
    "claude_code": {
      "config_file": "CLAUDE.md",
      "format": "Markdown",
      "recommended_sections": ["Project overview", "Build commands", "Test commands", "DO NOT rules", "Architecture"],
      "security_notes": ["Never include .env contents", "Keep secrets out of CLAUDE.md"],
      "best_practices": ["Use layered CLAUDE.md files for monorepos", "Include architecture diagrams"],
      "source_urls": ["https://docs.anthropic.com/..."]
    },
    "cursor": {
      "config_file": ".cursorrules",
      "format": "Plain text or Markdown",
      "recommended_sections": ["Tech stack", "Code style", "Testing approach", "Naming conventions"],
      "security_notes": ["Always create .cursorignore to exclude .env and secrets"],
      "best_practices": ["Keep under 500 tokens for performance", "Be explicit about frameworks"],
      "source_urls": []
    }
  },
  "stack_context": "Brief summary of the stack and how it affects config file content",
  "security_guidance": ["Stack-specific guidance retrieved for any scan findings, with the pattern name and fix"],
  "search_summary": "What searches were performed and key findings",
  "knowledge_sources_used": [
    {"source": "knowledge_base", "for": "FastAPI security patterns", "why": "curated, OWASP-mapped"},
    {"source": "web", "for": "current CLAUDE.md format docs", "why": "needs to be current"}
  ]
}

Return ONLY the JSON — no markdown fences, no preamble.
"""


def _get_tool_definitions(has_azure_search: bool = False) -> list[dict]:
    tools = []
    if has_azure_search:
        tools.append({
            "type": "function",
            "function": {
                "name": "search_security_kb",
                "description": (
                    "Search the curated Azure AI Search security knowledge base for threat patterns, "
                    "OWASP guidance, stack-specific remediation, and AI tool config best practices. "
                    "Use this FIRST for any security or configuration topic."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Natural language search query"},
                        "stack_filter": {
                            "type": "string",
                            "description": "Optional: filter by stack (python, fastapi, javascript, react, nodejs, django, express, flask)",
                        },
                        "threat_filter": {
                            "type": "string",
                            "description": "Optional: filter by threat type (injection, crypto, logging, config, secrets, auth)",
                        },
                    },
                    "required": ["query"],
                },
            },
        })
    tools.append({
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the web for current best practices, documentation, and security guidance. "
                "Use for current tool documentation and community practices. "
                "Fallback when the KB has no results for a query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"}
                },
                "required": ["query"],
            },
        },
    })
    return tools


def kb_search(query: str, stack_filter: str | None = None,
              threat_filter: str | None = None, top: int = 5) -> dict:
    """Query the Azure AI Search security KB. Module-level so the Researcher agent,
    the Remediator, and the capture script can all reuse it (it was previously a
    closure that only the Foundry tool loop could reach).

    Returns {"results": [...], "source"|"error"}. Each result: title, url, content
    (≤800 chars), threats, stacks.
    """
    from azure.search.documents import SearchClient
    from azure.core.credentials import AzureKeyCredential

    print(f"  [KB] kb_search query={query!r} stack={stack_filter} threat={threat_filter}", flush=True)

    endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT")
    key = os.environ.get("AZURE_SEARCH_KEY")
    index_name = os.environ.get("AZURE_SEARCH_INDEX", "fix-my-vibe-security-kb")

    if not endpoint or not key:
        return {"results": [], "error": "Azure AI Search not configured"}

    client = SearchClient(
        endpoint=endpoint,
        index_name=index_name,
        credential=AzureKeyCredential(key),
    )

    # Fold stack/threat hints into the query text — the index fields are not
    # marked filterable so OData filter expressions are unavailable.
    enriched_query = " ".join(filter(None, [query, stack_filter, threat_filter]))

    results = client.search(
        search_text=enriched_query,
        search_mode="any",
        select=["content", "source_url", "source_title", "threat_categories", "stack_applicable_to"],
        top=top,
    )

    formatted = []
    for r in results:
        formatted.append({
            "title": r.get("source_title", ""),
            "url": r.get("source_url", ""),
            "content": r.get("content", "")[:800],
            "threats": r.get("threat_categories", []),
            "stacks": r.get("stack_applicable_to", []),
        })

    print(f"  [KB] → {len(formatted)} results", flush=True)
    return {"results": formatted, "source": "azure_ai_search"}


def web_search(query: str) -> dict:
    """Tavily web search. Module-level companion to kb_search (fallback path)."""
    from tavily import TavilyClient
    tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    response = tavily.search(query=query, max_results=5)
    return {
        "results": [
            {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
            for r in response.get("results", [])
        ]
    }


def _make_tool_handlers() -> dict:
    # Thin adapters so the Foundry tool-calling loop (which passes an args dict)
    # reuses the same module-level functions everything else calls directly.
    return {
        "search_security_kb": lambda args: kb_search(
            args["query"], args.get("stack_filter"), args.get("threat_filter")
        ),
        "search_web": lambda args: web_search(args["query"]),
    }


def run(input: dict) -> dict:
    """
    Standalone interface: return static best-practice research without Azure.
    Used as fallback when Azure is unavailable.
    """
    detected_tools = input.get("detected_tools", [])
    detected_stack = input.get("detected_stack", [])

    static_research = {
        "claude_code": {
            "config_file": "CLAUDE.md",
            "format": "Markdown",
            "recommended_sections": [
                "Project overview and purpose",
                "Tech stack and key dependencies",
                "Build commands (npm run build, pip install, etc.)",
                "Test commands (pytest, npm test, etc.)",
                "Architecture and key files",
                "DO NOT rules (common mistakes to avoid)",
                "Code style and conventions",
            ],
            "security_notes": [
                "Never include .env contents or secrets",
                "Do not include credentials or API keys",
                "Avoid including large generated files",
            ],
            "best_practices": [
                "Start with a one-paragraph project summary",
                "List exact commands — don't say 'run the tests', say 'pytest tests/'",
                "Use DO NOT sections to prevent common mistakes",
                "Keep under 2000 tokens for optimal context usage",
                "For monorepos, use layered CLAUDE.md files per package",
            ],
        },
        "cursor": {
            "config_file": ".cursorrules",
            "format": "Plain text or Markdown",
            "recommended_sections": [
                "Tech stack and versions",
                "Code style and formatting rules",
                "Testing approach",
                "Naming conventions",
                "Import organization",
                "Error handling patterns",
            ],
            "security_notes": [
                "Always create .cursorignore to exclude .env, .env.local, secrets/",
                "Add *.pem, *.key, credentials.json to .cursorignore",
            ],
            "best_practices": [
                "Keep under 500 tokens for performance",
                "Be explicit about framework versions",
                "Include examples of preferred code patterns",
                "Specify TypeScript strictness level if applicable",
            ],
        },
        "copilot": {
            "config_file": ".github/copilot-instructions.md",
            "format": "Markdown",
            "recommended_sections": [
                "Project context",
                "Preferred patterns",
                "Libraries to use (and avoid)",
                "Testing requirements",
            ],
            "security_notes": [
                "GitHub Copilot sends context to GitHub servers — avoid including secrets",
            ],
            "best_practices": [
                "Focus on what Copilot should NOT do as much as what it should",
                "Reference your style guide if one exists",
                "Include security requirements explicitly",
            ],
        },
        "aider": {
            "config_file": ".aider.conf.yml",
            "format": "YAML",
            "recommended_sections": ["model", "auto-commits", "gitignore patterns"],
            "security_notes": ["Use .aiderignore to exclude .env files"],
            "best_practices": [
                "Set auto-commits: false for more control",
                "Specify your preferred model explicitly",
            ],
        },
        "windsurf": {
            "config_file": ".windsurfrc",
            "format": "YAML or Markdown",
            "recommended_sections": ["Project rules", "Code style"],
            "security_notes": ["Exclude sensitive files from Windsurf context"],
            "best_practices": ["Mirror your .cursorrules structure for consistency"],
        },
        "cline": {
            "config_file": ".clinerules",
            "format": "Markdown",
            "recommended_sections": ["Project context", "Coding guidelines"],
            "security_notes": ["Review what files Cline can access"],
            "best_practices": ["Keep concise and action-oriented"],
        },
        "continue": {
            "config_file": ".continue/config.json",
            "format": "JSON",
            "recommended_sections": ["models", "contextProviders", "slashCommands"],
            "security_notes": ["Store API keys in env vars, not in config.json"],
            "best_practices": ["Use context providers to feed relevant project context"],
        },
    }

    research_for_detected = {
        tool: static_research[tool]
        for tool in detected_tools
        if tool in static_research
    }

    stack_str = ", ".join(detected_stack) if detected_stack else "unknown"
    return {
        "research": research_for_detected,
        "stack_context": f"Project uses {stack_str}. Config files should reference the specific frameworks and their conventions.",
        "search_summary": "Static best-practice knowledge used (Azure unavailable in local mode)",
        "mode": "local",
    }


def run_with_foundry(client, scan_result: dict) -> dict:
    """Run the Researcher agent using Azure AI Foundry Agents API.

    o4-mini decides what to search for and how many queries to run based on
    the scan result. The model chooses queries — Python only executes them.
    KB-first: search_security_kb (Azure AI Search) is preferred for security
    topics; search_web (Tavily) is the fallback for novel/current queries.
    """
    has_azure_search = bool(os.environ.get("AZURE_SEARCH_ENDPOINT"))
    tools = _get_tool_definitions(has_azure_search)

    agent = client.agents.create_agent(
        model=os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"],
        name="fix-my-vibe-researcher",
        instructions=RESEARCHER_INSTRUCTIONS,
        tools=tools,
    )

    if has_azure_search:
        source_hint = (
            "You have BOTH the curated security knowledge base (search_security_kb) and web search — "
            "prefer the knowledge base for security topics, the web for current tool docs. "
            "The KB has OWASP, CWE, NIST, and framework-specific security patterns. "
        )
    else:
        source_hint = "Only web search is available in this run. "

    task_message = (
        "Research current best practices for the AI coding tools and stack detected in this project. "
        + source_hint +
        "Choose your own queries based on what was detected — cover each tool combined with the stack, "
        "and any code_security_findings types. "
        "Return a structured JSON research result including knowledge_sources_used.\n\n"
        f"Scan result:\n{json.dumps(scan_result, indent=2)}"
    )
    thread_id = create_thread_and_send(client, task_message)
    run_agent_with_tools(client, agent.id, thread_id, _make_tool_handlers(), max_iterations=400)
    raw, reasoning = get_last_assistant_message_with_reasoning(client, thread_id)
    result = parse_json_response(raw)
    result["_reasoning_trace"] = reasoning
    result["mode"] = "foundry"
    client.agents.delete_agent(agent.id)
    return result

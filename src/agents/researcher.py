"""
src/agents/researcher.py
Researcher agent: uses Tavily to fetch current best practices
for each detected AI tool and the project's stack.
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
You are the Researcher agent for Fix My Vibe. You use web search to find current best practices
for AI coding tool configuration.

You will be given a JSON scan result with detected_tools and detected_stack.

For each detected tool, search for:
1. The official documentation for its context/config file format
2. Current community best practices for that stack + tool combination
3. Any known security gotchas (e.g. what NOT to include in the context file)
4. Recommended sections for the config file given the detected stack

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
  "search_summary": "What searches were performed and key findings"
}

Return ONLY the JSON — no markdown fences, no preamble.
"""


def _get_tool_definitions() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": "Search the web for current best practices, documentation, and security guidance for AI coding tools and frameworks",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The search query"}
                    },
                    "required": ["query"],
                },
            },
        }
    ]


def _make_tool_handlers() -> dict:
    def search_web(args: dict) -> dict:
        from tavily import TavilyClient
        tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
        response = tavily.search(query=args["query"], max_results=5)
        return {
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                }
                for r in response.get("results", [])
            ]
        }
    return {"search_web": search_web}


def run(input: dict) -> dict:
    """
    Standalone interface: return static best-practice research without Bing.
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
        "search_summary": "Static best-practice knowledge used (Bing Grounding unavailable in local mode)",
        "mode": "local",
    }


def run_with_foundry(client, scan_result: dict) -> dict:
    """Run the Researcher agent using Azure AI Foundry Agents API.

    o4-mini decides what to search for and how many queries to run based on
    the scan result. The model chooses queries — Python only executes them.
    """
    agent = client.agents.create_agent(
        model=os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"],
        name="fix-my-vibe-researcher",
        instructions=RESEARCHER_INSTRUCTIONS,
        tools=_get_tool_definitions(),
    )
    task_message = (
        "Research current best practices for the AI coding tools and stack detected in this project. "
        "Use search_web to find official documentation, community best practices, and security guidance. "
        "Choose your own queries based on what was detected — search for each tool combined with the stack. "
        "Return a structured JSON research result.\n\n"
        f"Scan result:\n{json.dumps(scan_result, indent=2)}"
    )
    thread_id = create_thread_and_send(client, task_message)
    run_agent_with_tools(client, agent.id, thread_id, _make_tool_handlers())
    raw, reasoning = get_last_assistant_message_with_reasoning(client, thread_id)
    result = parse_json_response(raw)
    result["_reasoning_trace"] = reasoning
    result["mode"] = "foundry"
    client.agents.delete_agent(agent.id)
    return result

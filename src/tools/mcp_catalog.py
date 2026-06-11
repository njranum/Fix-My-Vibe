"""
src/tools/mcp_catalog.py
Curated MCP server catalogue for the Planner (M3).

Static, hand-curated mapping from detected stack to recommended MCP servers
with one-line install commands. Deliberately NOT live-researched: a curated
catalogue is deterministic, demo-reliable, and the install commands are
verified once by a human instead of trusted from a web result
(see docs/M3-DECISIONS.md, scope verdicts).
"""


# Each entry: which stacks it serves ("*" = any), why it helps, install one-liner.
# Install commands use the Claude Code syntax; other tools configure the same
# packages via their own MCP config files.
_MCP_CATALOG: list[dict] = [
    {
        "name": "GitHub",
        "stacks": ["*"],
        "why": "PR, issue, and repo operations without leaving the AI session",
        "install": "claude mcp add github -- npx -y @modelcontextprotocol/server-github",
    },
    {
        "name": "Postgres",
        "stacks": ["fastapi", "django", "flask"],
        "why": "schema inspection and read-only queries against your database, so the AI reasons over the real schema instead of guessing",
        "install": "claude mcp add postgres -- npx -y @modelcontextprotocol/server-postgres <connection-string>",
    },
    {
        "name": "Playwright",
        "stacks": ["react", "nextjs", "vite"],
        "why": "drives a real browser — the AI can open your app, click through flows, and verify UI changes",
        "install": "claude mcp add playwright -- npx -y @playwright/mcp@latest",
    },
    {
        "name": "Fetch",
        "stacks": ["python", "node"],
        "why": "fetches and converts web pages to markdown — useful for working against API docs",
        "install": "claude mcp add fetch -- uvx mcp-server-fetch",
    },
]

_MAX_RECOMMENDATIONS = 3


def recommend_mcp_servers(detected_stack: list[str]) -> list[dict]:
    """Return up to 3 MCP server recommendations for the detected stack,
    most stack-specific first."""
    stack = set(detected_stack)
    specific = [e for e in _MCP_CATALOG if stack & set(e["stacks"])]
    universal = [e for e in _MCP_CATALOG if e["stacks"] == ["*"]]
    recs = specific + universal
    return [
        {"name": e["name"], "why": e["why"], "install": e["install"]}
        for e in recs[:_MAX_RECOMMENDATIONS]
    ]

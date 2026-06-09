"""
src/agents/planner.py
Planner agent: pure reasoning — takes scan result + research and produces
a ranked ActionPlan. No file I/O, no web search — only inference.
"""

import os
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.foundry_utils import (
    get_last_assistant_message_with_reasoning,
    parse_json_response,
    create_thread_and_send,
    run_agent_with_tools,
)


PLANNER_INSTRUCTIONS = """
You are the Planner agent for Fix My Vibe. You reason carefully about what a developer
needs to fix in their AI coding tool setup and produce a prioritised, actionable plan.

You will receive a JSON object with:
- scan_result: full output from the Scanner agent
- research: best practices from the Researcher agent

Your job:
1. Analyse the gap between the current state (scan_result) and best practices (research)
2. Infer the project's coding conventions from the scan data (file structure, linters found, package files)
3. Rank all required fixes by impact and urgency
4. For each fix, specify EXACTLY what file to create/update and what sections it must contain
5. Generate the ACTUAL content for each config file, tailored to the detected stack and conventions

Output a JSON ActionPlan with this structure:
{
  "actions": [
    {
      "rank": 1,
      "tool": "claude_code",
      "action": "create",
      "file": "CLAUDE.md",
      "priority": "high",
      "reason": "Claude Code detected (claude CLI on PATH) but no CLAUDE.md exists",
      "content": "# Project Name\\n\\n## Overview\\n...full file content here...",
      "expected_sections": ["Overview", "Build commands", "Test commands", "DO NOT"],
      "estimated_tokens": 450
    },
    {
      "rank": 2,
      "tool": "security",
      "action": "create",
      "file": ".gitignore",
      "priority": "high",
      "reason": ".env file present but .gitignore missing — secrets at risk",
      "content": ".env\\n.env.local\\n__pycache__/\\n*.pyc\\n",
      "expected_sections": [".env"],
      "estimated_tokens": 30
    }
  ],
  "security_actions": [
    {
      "rank": 1,
      "issue_type": "exposed_env",
      "file_to_create": ".gitignore",
      "description": "Add .env to .gitignore immediately"
    }
  ],
  "convention_summary": "Python project using FastAPI. Uses pytest for testing, ruff for linting. Snake_case file naming.",
  "plan_summary": "3 actions required. 2 high priority (security + missing CLAUDE.md), 1 medium (improve .cursorrules).",
  "total_actions": 3
}

Rules for generating config file content:
- CLAUDE.md: Include project overview (inferred from stack + dir structure), exact build/test commands found,
  detected linters, key directories, and DO NOT rules based on common mistakes for the stack
- .cursorrules: Tailored to the stack with specific framework versions, naming conventions, error handling patterns
- .cursorignore: Must include .env, .env.local, .env.*, *.pem, *.key, node_modules/, __pycache__/
- .gitignore: Comprehensive for the detected stack
- Security actions are ALWAYS rank 1 — never deprioritize security fixes

Return ONLY the JSON — no markdown fences, no preamble.
"""


def _generate_claude_md(scan_result: dict, conventions: dict) -> str:
    """Generate CLAUDE.md content from scan result and conventions."""
    stack = scan_result.get("detected_stack", [])
    linters = scan_result.get("detected_linters", [])
    key_dirs = conventions.get("key_directories", [])
    test_cmd = conventions.get("test_command")
    build_cmd = conventions.get("build_command")
    lint_cmd = conventions.get("lint_command")

    project_path = scan_result.get("project_path", "")
    project_name = Path(project_path).name if project_path else "Project"

    lines = [
        f"# {project_name}",
        "",
        "## Overview",
        f"<!-- Add a 1-2 sentence description of what this project does -->",
        "",
        "## Stack",
    ]
    if stack:
        for s in stack:
            lines.append(f"- {s}")
    lines.extend(["", "## Commands"])

    if test_cmd:
        lines.append(f"- **Test:** `{test_cmd}`")
    else:
        lines.append("- **Test:** `<!-- add test command -->`")

    if build_cmd:
        lines.append(f"- **Build:** `{build_cmd}`")

    if lint_cmd:
        lines.append(f"- **Lint:** `{lint_cmd}`")
    elif "ruff.toml" in linters or ".ruff.toml" in linters:
        lines.append("- **Lint:** `ruff check .`")
    elif ".flake8" in linters:
        lines.append("- **Lint:** `flake8`")
    elif ".eslintrc" in linters or "eslint.config.js" in linters:
        lines.append("- **Lint:** `eslint .`")

    if key_dirs:
        lines.extend(["", "## Key Directories"])
        for d in key_dirs:
            lines.append(f"- `{d}/`")

    lines.extend([
        "",
        "## DO NOT",
        "- Do not use raw `open()` — use the project's I/O utilities",
        "- Do not commit .env files or secrets",
        "- Do not skip tests when fixing bugs",
        "- Do not use `any` type in TypeScript" if "typescript" in stack else "- Do not hardcode paths",
    ])

    return "\n".join(lines) + "\n"


def _generate_cursorrules(scan_result: dict, conventions: dict) -> str:
    stack = scan_result.get("detected_stack", [])
    stack_str = ", ".join(stack) if stack else "not specified"
    test_cmd = conventions.get("test_command", "")
    lint_cmd = conventions.get("lint_command", "")

    lines = [
        f"Tech stack: {stack_str}",
        "",
        "Code style:",
        "- Use consistent naming conventions throughout",
        "- Add type hints / TypeScript types everywhere" if "typescript" in stack or "python" in stack else "- Follow language conventions",
        "- Keep functions small and focused",
        "",
        "Testing:",
        f"- Run tests with: {test_cmd}" if test_cmd else "- Write tests for all new functionality",
        "- Do not skip failing tests",
        "",
        "Linting:",
        f"- Lint with: {lint_cmd}" if lint_cmd else "- Follow the project's linter configuration",
        "",
        "Security:",
        "- Never hardcode credentials or API keys",
        "- All sensitive config via environment variables",
    ]
    return "\n".join(lines) + "\n"


def _generate_cursorignore() -> str:
    return "\n".join([
        "# Secrets and environment",
        ".env",
        ".env.local",
        ".env.*.local",
        ".env.production",
        ".env.staging",
        "*.pem",
        "*.key",
        "*.p12",
        "secrets/",
        "credentials.json",
        "",
        "# Dependencies",
        "node_modules/",
        "__pycache__/",
        ".venv/",
        "venv/",
        "",
        "# Build output",
        "dist/",
        "build/",
        ".next/",
        "",
        "# IDE and OS",
        ".DS_Store",
        "*.log",
    ]) + "\n"


def _generate_gitignore(stack: list[str]) -> str:
    lines = ["# Environment and secrets", ".env", ".env.local", ".env.*", "*.pem", "*.key", ""]
    if "python" in stack:
        lines.extend(["# Python", "__pycache__/", "*.pyc", "*.pyo", ".venv/", "venv/", "*.egg-info/", "dist/", "build/", ".pytest_cache/", ""])
    if "node" in stack or "nextjs" in stack or "react" in stack:
        lines.extend(["# Node", "node_modules/", ".next/", "dist/", "build/", ""])
    if "typescript" in stack:
        lines.extend(["# TypeScript", "*.js.map", "*.d.ts.map", ""])
    lines.extend(["# OS", ".DS_Store", "Thumbs.db", "", "# IDE", ".vscode/settings.json", "*.swp"])
    return "\n".join(lines) + "\n"


def run(input: dict) -> dict:
    """
    Standalone interface: run planner without Foundry (pure local reasoning).
    """
    scan_result = input.get("scan_result", {})
    research = input.get("research", {})
    conventions = scan_result.get("conventions", {})

    detected_tools = scan_result.get("detected_tools", [])
    missing_configs = scan_result.get("missing_configs", {})
    security_issues = scan_result.get("security_issues", [])
    stack = scan_result.get("detected_stack", [])

    actions: list[dict] = []
    security_actions: list[dict] = []
    rank = 1

    # Security actions first (always highest priority)
    for issue in security_issues:
        if issue.get("type") == "exposed_env":
            if not scan_result.get("has_gitignore"):
                security_actions.append({
                    "rank": rank,
                    "issue_type": "exposed_env",
                    "file_to_create": ".gitignore",
                    "description": f"Add .env to .gitignore — {issue.get('file', '.env')} is unprotected",
                })
                actions.append({
                    "rank": rank,
                    "tool": "security",
                    "action": "create",
                    "file": ".gitignore",
                    "priority": "high",
                    "reason": f"{issue.get('file', '.env')} present but .gitignore missing — secrets at risk",
                    "content": _generate_gitignore(stack),
                    "expected_sections": [".env"],
                    "estimated_tokens": 50,
                })
                rank += 1

        if issue.get("type") == "missing_cursorignore":
            security_actions.append({
                "rank": rank,
                "issue_type": "missing_cursorignore",
                "file_to_create": ".cursorignore",
                "description": "Create .cursorignore to prevent Cursor from reading .env files",
            })
            actions.append({
                "rank": rank,
                "tool": "cursor",
                "action": "create",
                "file": ".cursorignore",
                "priority": "high",
                "reason": "Cursor detected without .cursorignore — .env files accessible to Cursor AI",
                "content": _generate_cursorignore(),
                "expected_sections": [".env"],
                "estimated_tokens": 40,
            })
            rank += 1

    # Missing config files
    for tool, config_file in missing_configs.items():
        if tool == "claude_code":
            content = _generate_claude_md(scan_result, conventions)
            expected = ["Overview", "Stack", "Commands", "DO NOT"]
        elif tool == "cursor":
            content = _generate_cursorrules(scan_result, conventions)
            expected = ["Tech stack", "Code style", "Testing", "Security"]
        else:
            tool_research = research.get("research", {}).get(tool, {})
            sections = tool_research.get("recommended_sections", ["Project context", "Code style"])
            content = f"# {tool} configuration\n\n" + "\n\n".join(
                f"## {s}\n<!-- Add content here -->" for s in sections
            ) + "\n"
            expected = sections

        actions.append({
            "rank": rank,
            "tool": tool,
            "action": "create",
            "file": config_file,
            "priority": "high",
            "reason": f"{tool} detected (via {', '.join(scan_result.get('tool_evidence', {}).get(tool, {}).get('evidence', ['config file']))}) but {config_file} is missing",
            "content": content,
            "expected_sections": expected,
            "estimated_tokens": len(content) // 4,
        })
        rank += 1

    # Improvements to existing configs with quality concerns
    for tool, config_data in scan_result.get("existing_configs", {}).items():
        audit = config_data.get("audit", {})
        concerns = audit.get("quality_concerns", [])
        if concerns:
            actions.append({
                "rank": rank,
                "tool": tool,
                "action": "improve",
                "file": config_data.get("filename", ""),
                "priority": "medium",
                "reason": f"Existing {config_data.get('filename')} has quality issues: {'; '.join(concerns[:2])}",
                "content": None,
                "expected_sections": research.get("research", {}).get(tool, {}).get("recommended_sections", []),
                "estimated_tokens": 0,
            })
            rank += 1

    stack_str = ", ".join(stack) if stack else "unknown stack"
    linters = scan_result.get("detected_linters", [])
    linter_str = f", {linters[0]}" if linters else ""
    convention_summary = f"Project uses {stack_str}{linter_str}."
    if conventions.get("test_command"):
        convention_summary += f" Tests run with `{conventions['test_command']}`."
    if conventions.get("package_manager"):
        convention_summary += f" Package manager: {conventions['package_manager']}."

    high_count = sum(1 for a in actions if a.get("priority") == "high")
    med_count = sum(1 for a in actions if a.get("priority") == "medium")
    plan_summary = f"{len(actions)} action(s) required."
    if high_count:
        plan_summary += f" {high_count} high priority."
    if med_count:
        plan_summary += f" {med_count} medium priority."

    return {
        "actions": actions,
        "security_actions": security_actions,
        "convention_summary": convention_summary,
        "plan_summary": plan_summary,
        "total_actions": len(actions),
    }


def run_with_foundry(client, scan_result: dict, research: dict) -> dict:
    """Run the Planner agent using Azure AI Foundry."""
    model = os.environ.get("FOUNDRY_MODEL_DEPLOYMENT_NAME", "Phi-4-reasoning")

    agent = client.agents.create_agent(
        model=model,
        name="fix-my-vibe-planner",
        instructions=PLANNER_INSTRUCTIONS,
        tools=[],
    )

    try:
        prompt = (
            "Analyse this scan result and research, then produce an ActionPlan.\n\n"
            f"scan_result:\n{json.dumps(scan_result, indent=2)}\n\n"
            f"research:\n{json.dumps(research, indent=2)}"
        )
        thread_id = create_thread_and_send(client, prompt)

        run = client.agents.runs.create_and_process(
            thread_id=thread_id,
            agent_id=agent.id,
        )

        if run.status != "completed":
            raise RuntimeError(f"Planner run ended with status: {run.status}")

        result_text, reasoning = get_last_assistant_message_with_reasoning(client, thread_id)
        result = parse_json_response(result_text)
        if reasoning:
            result["_reasoning_trace"] = reasoning
        return result

    finally:
        client.agents.delete_agent(agent.id)

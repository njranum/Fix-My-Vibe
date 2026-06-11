"""
src/agents/scanner.py
Scanner agent: wraps three-layer detection in a Foundry agent with function calling.
Produces a structured ScanResult that feeds into Researcher and Planner.
"""

import os
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.tools.fs_tools import (
    scan_directory,
    check_path_tools,
    check_vscode_extensions,
    read_existing_context_file,
    infer_project_conventions,
)
from src.tools.security_scan import scan_security_patterns
from src.foundry_utils import (
    run_agent_with_tools,
    get_last_assistant_message_with_reasoning,
    parse_json_response,
    create_thread_and_send,
)


SCANNER_INSTRUCTIONS = """
You are the Scanner agent for Fix My Vibe, a tool that diagnoses AI coding tool setup in developer projects.

Your job: analyse a developer's project directory and produce a complete, accurate diagnosis
of their AI coding tool setup. You use the tools available to you — never guess or fabricate.

Process (follow exactly):
1. Call scan_directory(project_path) — file-based analysis, layer 1
2. Call check_path_tools() — PATH-based detection, layer 2
3. Call check_vscode_extensions(project_path) — VS Code extension detection, layer 3
4. For each existing context file found (e.g. CLAUDE.md, .cursorrules), call read_existing_context_file()
5. Call infer_project_conventions(project_path) — detect build/test commands, naming conventions
6. Call scan_security_patterns(project_path) — code-level scan for patterns AI assistants
   commonly introduce (hardcoded secrets, eval(), SQL injection, verify=False, debug=True)
7. Synthesise all results into a single JSON diagnosis

Your output MUST be valid JSON matching this exact structure:
{
  "detected_tools": ["claude_code", "cursor"],
  "tool_evidence": {"claude_code": {"evidence": ["config:CLAUDE.md"]}},
  "detected_stack": ["python", "fastapi"],
  "is_monorepo": false,
  "security_issues": [
    {"type": "exposed_env", "file": ".env", "severity": "high", "description": "..."}
  ],
  "code_security_findings": [
    {"type": "hardcoded_secret", "file": "src/app.py", "line": 12, "severity": "high",
     "description": "...", "snippet": "...", "recommendation": "..."}
  ],
  "missing_configs": {"cursor": ".cursorrules"},
  "existing_configs": {
    "claude_code": {"audit": {"line_count": 100, "quality_concerns": []}}
  },
  "conventions": {
    "test_command": "pytest",
    "build_command": null,
    "lint_command": "ruff check .",
    "package_manager": null,
    "key_directories": ["src", "tests"]
  },
  "path_tools": {"aider": "aider"},
  "vscode_tools": [],
  "diagnosis_summary": "One paragraph plain English summary of what was found and what needs fixing.",
  "priority": "high"
}

Rules:
- "priority" is "high" if there are security issues, code_security_findings with high severity,
  or a detected tool has no config file at all
- Copy code_security_findings verbatim from the scan_security_patterns tool output — never
  invent, drop, or rewrite findings
- "priority" is "medium" if configs exist but have quality concerns
- "priority" is "low" if everything looks well-configured
- If no tools detected via any layer, set detected_tools to [] and note in diagnosis_summary
- Return ONLY the JSON — no markdown fences, no preamble
"""


def _get_tool_definitions() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "scan_directory",
                "description": "Scan a project directory for AI tool config files, stack signatures, and security issues (Layer 1)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_path": {"type": "string", "description": "Absolute path to the project directory"}
                    },
                    "required": ["project_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_path_tools",
                "description": "Check which AI coding tools are installed on the system PATH (Layer 2)",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_vscode_extensions",
                "description": "Inspect .vscode/extensions.json for AI tool extension IDs (Layer 3)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_path": {"type": "string", "description": "Absolute path to the project directory"}
                    },
                    "required": ["project_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_existing_context_file",
                "description": "Read and audit an existing AI context file (CLAUDE.md, .cursorrules, etc.)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_path": {"type": "string"},
                        "filename": {"type": "string", "description": "e.g. 'CLAUDE.md' or '.cursorrules'"},
                    },
                    "required": ["project_path", "filename"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "infer_project_conventions",
                "description": "Deeply infer project conventions: test commands, build commands, naming style",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_path": {"type": "string"}
                    },
                    "required": ["project_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "scan_security_patterns",
                "description": "Scan source code for security patterns AI assistants commonly introduce: hardcoded secrets, eval/exec, SQL string interpolation, verify=False, debug=True, shell=True",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_path": {"type": "string", "description": "Absolute path to the project directory"}
                    },
                    "required": ["project_path"],
                },
            },
        },
    ]


def _make_tool_handlers(project_path: str) -> dict:
    return {
        "scan_directory": lambda args: scan_directory(args["project_path"]),
        "check_path_tools": lambda _args: check_path_tools(),
        "check_vscode_extensions": lambda args: check_vscode_extensions(args["project_path"]),
        "read_existing_context_file": lambda args: read_existing_context_file(
            args["project_path"], args["filename"]
        ),
        "infer_project_conventions": lambda args: infer_project_conventions(args["project_path"]),
        "scan_security_patterns": lambda args: scan_security_patterns(args["project_path"]),
    }


def run(input: dict) -> dict:
    """
    Standalone interface: run scanner without Foundry (pure Python, local tools only).
    Used for testing and as fallback when Azure is unavailable.
    """
    project_path = input.get("project_path", "")
    from src.tools.detection import run_full_detection

    detection = run_full_detection(project_path)
    conventions = infer_project_conventions(project_path)
    code_scan = scan_security_patterns(project_path)
    code_findings = code_scan.get("findings", [])

    existing_configs = {}
    for tool, files in detection.get("tool_files_found", {}).items():
        if files:
            audit = read_existing_context_file(project_path, files[0])
            if audit.get("exists"):
                existing_configs[tool] = {"filename": files[0], "audit": audit}

    priority = "low"
    if detection.get("security_issues"):
        priority = "high"
    elif any(f.get("severity") == "high" for f in code_findings):
        priority = "high"
    elif detection.get("missing_configs"):
        priority = "high"
    elif any(
        c.get("quality_concerns") for c in existing_configs.values()
        if isinstance(c, dict) and "audit" in c
    ):
        priority = "medium"

    tools = detection.get("detected_tools", [])
    missing = detection.get("missing_configs", {})
    sec = detection.get("security_issues", [])
    stack = detection.get("detected_stack", [])

    summary_parts = []
    if tools:
        summary_parts.append(f"Detected AI tools: {', '.join(tools)}.")
    else:
        summary_parts.append("No AI coding tools detected via config files, PATH, or VS Code extensions.")
    if stack:
        summary_parts.append(f"Stack: {', '.join(stack)}.")
    if missing:
        summary_parts.append(f"Missing configs: {', '.join(f'{t} needs {f}' for t, f in missing.items())}.")
    if sec:
        summary_parts.append(f"{len(sec)} security issue(s) found.")
    if code_findings:
        high = sum(1 for f in code_findings if f.get("severity") == "high")
        summary_parts.append(
            f"{len(code_findings)} code-level security finding(s) ({high} high severity) — "
            "patterns AI assistants commonly introduce."
        )

    # PROMPTS.md is a Fix My Vibe output, not a tool config — track presence so
    # the Planner offers to create it only when missing
    has_prompts_md = bool(read_existing_context_file(project_path, "PROMPTS.md").get("exists"))

    return {
        "project_path": detection.get("project_path", project_path),
        "has_prompts_md": has_prompts_md,
        "detected_tools": tools,
        "tool_evidence": detection.get("tool_evidence", {}),
        "detected_stack": stack,
        "is_monorepo": detection.get("is_monorepo", False),
        "security_issues": sec,
        "code_security_findings": code_findings,
        "missing_configs": missing,
        "existing_configs": existing_configs,
        "has_gitignore": detection.get("has_gitignore", False),
        "gitignore_content": detection.get("gitignore_content", ""),
        "detected_linters": detection.get("detected_linters", []),
        "conventions": conventions,
        "path_tools": detection.get("path_tools", {}),
        "vscode_tools": detection.get("vscode_tools", []),
        "diagnosis_summary": " ".join(summary_parts),
        "priority": priority,
    }


def run_with_foundry(client, project_path: str) -> dict:
    """Run the Scanner agent using Azure AI Foundry Agents API.

    o4-mini receives tool definitions and decides which tools to call,
    in what order, and what to look for — Python only executes the calls.
    """
    abs_path = str(Path(project_path).resolve())
    agent = client.agents.create_agent(
        model=os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"],
        name="fix-my-vibe-scanner",
        instructions=SCANNER_INSTRUCTIONS,
        tools=_get_tool_definitions(),
    )
    task_message = (
        f"Scan the project at {abs_path}. "
        "Use the available tools to: detect AI coding tools (config files, PATH, VS Code extensions), "
        "identify the tech stack, find security issues (exposed .env, missing .cursorignore), "
        "scan source code for security patterns AI assistants commonly introduce, "
        "audit any existing AI config files for quality, and infer project conventions. "
        "Return a complete ScanResult JSON."
    )
    thread_id = create_thread_and_send(client, task_message)
    run_agent_with_tools(client, agent.id, thread_id, _make_tool_handlers(abs_path), max_iterations=400)
    raw, reasoning = get_last_assistant_message_with_reasoning(client, thread_id)
    result = parse_json_response(raw)
    result["_reasoning_trace"] = reasoning
    result.setdefault("project_path", abs_path)
    # Deterministic post-check (not model-reported): does PROMPTS.md already exist?
    result["has_prompts_md"] = bool(
        read_existing_context_file(abs_path, "PROMPTS.md").get("exists")
    )
    client.agents.delete_agent(agent.id)
    return result

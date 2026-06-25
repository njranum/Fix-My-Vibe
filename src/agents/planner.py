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
from src.tools.mcp_catalog import recommend_mcp_servers


PLANNER_INSTRUCTIONS = """
You are the Planner agent for Fix My Vibe. You reason carefully about what a developer
needs to fix in their AI coding tool setup and produce a prioritised, actionable plan.

You will receive a JSON object with:
- scan_result: full output from the Scanner agent
- research: best practices from the Researcher agent

Your job:
1. Analyse the gap between the current state (scan_result) and best practices (research)
2. Rank all required fixes by impact and urgency (security always first)
3. Generate the ACTUAL complete file content for each config file

CRITICAL CONTENT RULES:
- Write COMPLETE file content for every action — no placeholders, no "add here" comments, no template markers
- Two different projects MUST produce different CLAUDE.md files — base content on the actual scan data
- For CLAUDE.md: use readme_summary from conventions as the Overview; include the exact test_command,
  build_command, lint_command detected; reference actual key_directories; add DO NOT rules specific to the stack
- If the input includes mcp_recommendations, add a "## Recommended MCP servers" section to CLAUDE.md:
  one bullet per server with its "why" and its install command in backticks, copied VERBATIM —
  never invent or alter MCP server names or install commands
- For .cursorrules: reference the actual detected stack and frameworks; include detected linting/test tools
- For .cursorignore: always include .env, .env.*, *.pem, *.key, node_modules/, __pycache__/, .venv/
- For .gitignore: generate a comprehensive file appropriate to the detected stack
- If scan_result.code_security_findings is non-empty, add a SECURITY.md action (priority high if any
  finding is high severity). Render EVERY finding with its exact file, line, snippet, and recommendation
  copied from the scan data — never invent, drop, or reword findings. Findings are facts from a
  deterministic scanner; your job is structure and prioritisation, not re-detection.
  SECURITY.md format: title "# Security Audit — <project name>"; one-line framing that these are
  "code patterns AI coding assistants commonly introduce"; "## Summary" with finding counts by severity;
  "## High severity" then "## Medium severity" sections, each finding as
  "### `file:line` — description" followed by the snippet in a ``` code fence and "**Fix:** <recommendation>";
  end with "## Next steps".
- If AI tools are detected and scan_result.has_prompts_md is false, add a PROMPTS.md action
  (priority medium, after config fixes). Generate 5-8 copy-paste-ready prompts tailored to the
  ACTUAL detected stack and conventions — every prompt must reference the real test command,
  real key directories, or real frameworks found in the scan; a FastAPI project and a Next.js
  project must get different prompts. Format: "# Prompt Library — <project name>"; "## How to use";
  "## Everyday development", "## Code quality", "## Stack-specific" sections; each prompt as
  "### <title>" with the prompt text in a ``` code fence, using <placeholders> for the parts
  the developer fills in. Always include one prompt for reviewing code for AI-introduced
  security patterns.
- Security actions are ALWAYS rank 1 — never deprioritize security fixes

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
      "content": "# cursor-project\\n\\n## Overview\\nA FastAPI REST API...\\n\\n## Commands\\n- **Test:** `pytest`\\n...",
      "expected_sections": ["Overview", "Commands", "DO NOT"],
      "estimated_tokens": 450
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
  "convention_summary": "Python project using FastAPI. Tests run with pytest. Lint with ruff.",
  "plan_summary": "3 actions required. 2 high priority, 1 medium.",
  "total_actions": 3
}

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
    type_check_cmd = conventions.get("type_check_command")
    readme_summary = conventions.get("readme_summary")

    project_path = scan_result.get("project_path", "")
    project_name = Path(project_path).name if project_path else "Project"

    lines = [f"# {project_name}", ""]

    # Overview: use README summary if available, otherwise placeholder
    lines.append("## Overview")
    if readme_summary:
        lines.append(readme_summary)
    else:
        lines.append("<!-- Add a 1-2 sentence description of what this project does -->")
    lines.append("")

    # Stack
    lines.append("## Stack")
    if stack:
        for s in stack:
            lines.append(f"- {s}")
    if conventions.get("python_version"):
        lines.append(f"- Python {conventions['python_version']}")
    if conventions.get("package_manager"):
        lines.append(f"- Package manager: {conventions['package_manager']}")
    lines.append("")

    # Commands
    lines.append("## Commands")
    if test_cmd:
        lines.append(f"- **Test:** `{test_cmd}`")
    else:
        lines.append("- **Test:** `<!-- add test command, e.g. pytest -->`")
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
    if type_check_cmd:
        lines.append(f"- **Type check:** `{type_check_cmd}`")
    if conventions.get("pre_commit"):
        lines.append("- **Pre-commit:** `pre-commit run --all-files`")
    lines.append("")

    # Architecture
    if key_dirs:
        lines.append("## Architecture")
        for d in key_dirs:
            lines.append(f"- `{d}/`")
        lines.append("")

    # MCP server recommendations — from the curated catalogue, stack-matched
    mcp_recs = recommend_mcp_servers(stack)
    if mcp_recs:
        lines.append("## Recommended MCP servers")
        lines.append("Extend your AI tool with these MCP servers (matched to this stack):")
        for rec in mcp_recs:
            lines.append(f"- **{rec['name']}** — {rec['why']}")
            lines.append(f"  - Install: `{rec['install']}`")
        lines.append("")

    # DO NOT rules — tailored to stack
    lines.append("## DO NOT")
    lines.append("- Do not commit `.env` files or secrets")
    lines.append("- Do not skip tests when fixing bugs")
    if "python" in stack:
        lines.append("- Do not use bare `except:` — always catch specific exceptions")
        lines.append("- Do not use `print()` for logging — use the logging module")
    if "typescript" in stack or "nextjs" in stack or "react" in stack:
        lines.append("- Do not use `any` type — always specify proper TypeScript types")
        lines.append("- Do not import from `@/` without checking the tsconfig paths")
    if "fastapi" in stack or "django" in stack:
        lines.append("- Do not expose raw database errors to API responses")
    if conventions.get("naming_conventions", {}).get("python_files") == "snake_case":
        lines.append("- Do not use camelCase for Python file or variable names — snake_case only")

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


def _generate_prompts_md(scan_result: dict, conventions: dict) -> str:
    """Generate PROMPTS.md — a starter prompt library tailored to the detected
    stack and conventions. Every prompt embeds real project specifics (test
    command, key directories) so sessions start grounded, not generic."""
    stack = scan_result.get("detected_stack", [])
    key_dirs = conventions.get("key_directories", []) or ["src"]
    dirs_str = ", ".join(f"`{d}/`" for d in key_dirs)
    test_cmd = conventions.get("test_command") or (
        "pytest" if "python" in stack else "npm test" if "node" in stack else "the project's test command"
    )
    lint_cmd = conventions.get("lint_command")

    project_path = scan_result.get("project_path", "")
    project_name = Path(project_path).name if project_path else "Project"
    stack_str = ", ".join(stack) if stack else "this project"

    def prompt(title: str, body: str) -> list[str]:
        return [f"### {title}", "", "```", body, "```", ""]

    lines = [
        f"# Prompt Library — {project_name}",
        "",
        f"_Starter prompts for AI coding sessions in this project ({stack_str})._",
        "_Generated by Fix My Vibe from your actual stack and conventions._",
        "",
        "## How to use",
        "Copy a prompt, replace the `<placeholders>`, and paste it into Claude Code,",
        "Cursor, or your AI tool of choice. Each prompt already references this",
        "project's real commands and structure.",
        "",
        "## Everyday development",
        "",
    ]

    verify_clause = f"verify with `{test_cmd}`"
    if lint_cmd:
        verify_clause += f" and `{lint_cmd}`"

    lines += prompt(
        "Implement a feature",
        f"Implement <feature description>. Look at the existing code in {dirs_str} first and "
        f"follow its patterns and naming. Write tests alongside the change, then {verify_clause}.",
    )
    lines += prompt(
        "Write tests for existing code",
        f"Write tests for <file or function>. Match the style of the existing tests, cover the "
        f"main path plus edge cases (empty input, invalid input, error paths), and run `{test_cmd}` to confirm they pass.",
    )
    lines += prompt(
        "Debug a failure",
        f"This is failing: <paste error or failing test output>. Diagnose the root cause before "
        f"proposing any fix — trace the data flow through {dirs_str} and explain what's happening. "
        f"Then propose the smallest fix and {verify_clause}.",
    )

    lines += ["## Code quality", ""]
    lines += prompt(
        "Refactor safely",
        f"Refactor <file or function> for readability without changing behaviour. "
        f"Keep the public interface identical, and run `{test_cmd}` before and after to prove nothing broke.",
    )
    lines += prompt(
        "Security review (AI-introduced patterns)",
        "Review <file> for security issues AI assistants commonly introduce: hardcoded secrets, "
        "SQL built with string interpolation, eval/exec on user input, disabled TLS verification, "
        "debug mode left on, and missing input validation. Report findings with line numbers and "
        "severity — do not change any code yet.",
    )

    # Stack-specific prompts
    stack_prompts: list[str] = []
    if "fastapi" in stack:
        stack_prompts += prompt(
            "Add a FastAPI endpoint",
            f"Add a <method> endpoint at <path> that <does what>. Use Pydantic models for request "
            f"and response validation, follow the routing patterns already in {dirs_str}, handle "
            f"errors with proper HTTP status codes (never expose raw exceptions), and {verify_clause}.",
        )
        stack_prompts += prompt(
            "Write a pytest fixture",
            "Create a pytest fixture for <service or dependency> so tests don't hit real "
            "external services. Use FastAPI's TestClient and dependency overrides; put shared "
            "fixtures in conftest.py.",
        )
    if "django" in stack:
        stack_prompts += prompt(
            "Add a Django view",
            f"Add a view that <does what>: URL pattern, form/serializer validation, and template or "
            f"JSON response following the existing app structure in {dirs_str}. Then {verify_clause}.",
        )
    if "react" in stack or "nextjs" in stack:
        stack_prompts += prompt(
            "Add a React component",
            f"Create a <ComponentName> component that <does what>. Use the prop and state patterns "
            f"from the existing components in {dirs_str}, type all props explicitly"
            + (", and add a test" if test_cmd else "")
            + ".",
        )
    if "typescript" in stack:
        stack_prompts += prompt(
            "Tighten types",
            "Find and fix loose typing in <file>: replace any `any`, add missing return types, "
            "and narrow unions where possible. Do not change runtime behaviour.",
        )
    if stack_prompts:
        lines += ["## Stack-specific", ""]
        lines += stack_prompts

    return "\n".join(lines).rstrip("\n") + "\n"


def _generate_security_md(scan_result: dict) -> str:
    """Generate SECURITY.md from code-level findings — the AI code audit report."""
    findings = scan_result.get("code_security_findings", [])
    project_path = scan_result.get("project_path", "")
    project_name = Path(project_path).name if project_path else "Project"

    high = [f for f in findings if f.get("severity") == "high"]
    medium = [f for f in findings if f.get("severity") == "medium"]

    lines = [
        f"# Security Audit — {project_name}",
        "",
        "_Generated by Fix My Vibe. These are code patterns that AI coding assistants",
        "commonly introduce — review each finding and fix or explicitly accept it._",
        "",
        "## Summary",
        f"- **{len(findings)}** finding(s): {len(high)} high severity, {len(medium)} medium severity",
        "",
    ]

    def _render(group: list[dict], heading: str) -> None:
        if not group:
            return
        lines.append(f"## {heading}")
        lines.append("")
        for f in group:
            lines.append(f"### `{f.get('file', '?')}:{f.get('line', '?')}` — {f.get('description', '')}")
            snippet = f.get("snippet")
            if snippet:
                lines.append("")
                lines.append("```")
                lines.append(snippet)
                lines.append("```")
            rec = f.get("recommendation")
            if rec:
                lines.append(f"**Fix:** {rec}")
            lines.append("")

    _render(high, "High severity")
    _render(medium, "Medium severity")

    lines.extend([
        "## Next steps",
        "- Fix high-severity findings before the next commit — assume any hardcoded credential is compromised",
        "- Add these patterns to your AI tool's config (CLAUDE.md / .cursorrules DO NOT rules) so they are not reintroduced",
        "- Re-run `fix-my-vibe` after fixing to verify",
    ])
    return "\n".join(lines) + "\n"


def _build_remediation_actions(scan_result: dict, start_rank: int) -> tuple[list[dict], int]:
    """Build VERIFIED Tier-A code-remediation actions from code findings.

    For each deterministically-fixable finding (Python only in v1): re-read the real
    source line (never trust the lossy/truncated scanner snippet), propose an
    idiom-guarded fix, and PROVE in memory that the patched file parses, clears the
    finding, and introduces nothing new. Only verified fixes become actions; the rest
    stay in SECURITY.md. Pure planning — never writes.
    """
    from src.tools import code_fixes
    from src.tools import remediation as rem

    project_path = scan_result.get("project_path", "")
    findings = scan_result.get("code_security_findings", [])
    actions: list[dict] = []
    rank = start_rank
    file_cache: dict[str, str | None] = {}

    for finding in findings:
        ftype = finding.get("type")
        if ftype not in code_fixes.DETERMINISTIC_TYPES:
            continue
        rel = finding.get("file", "")
        if not rel.endswith(".py"):          # v1: Python only (JS/TS report-only)
            continue
        line_no = finding.get("line")
        if not line_no:
            continue

        if rel not in file_cache:
            try:
                file_cache[rel] = (Path(project_path) / rel).read_text(encoding="utf-8")
            except OSError:
                file_cache[rel] = None
        text = file_cache[rel]
        if text is None:
            continue

        lines = text.splitlines()
        if line_no > len(lines):
            continue
        actual_line = lines[line_no - 1]

        proposal = code_fixes.propose_fix(ftype, actual_line)
        if proposal is None:
            continue
        proposed_line, rationale = proposal

        add_imports = rem.detect_needed_imports(text, proposed_line)
        try:
            patched = rem.build_patched(text, line_no, actual_line, proposed_line, add_imports)
        except ValueError:
            continue
        verdict = rem.verify_patch(text, patched, ftype, is_python=True, file_label=rel)
        if not verdict.get("ok"):
            continue

        actions.append({
            "rank": rank,
            "tool": "security",
            "action": "remediate",
            "file": rel,
            "line": line_no,
            "finding_type": ftype,
            "severity": finding.get("severity", "medium"),
            "tier": "deterministic",
            "expected_line": actual_line,
            "proposed_line": proposed_line,
            "add_imports": add_imports,
            "patch": rem.make_unified_diff(text, patched, rel),
            "rationale": rationale,
            "kb_citations": [],
            "requires_followup": None,
            "confidence": "high",
            "verification": verdict,
            "priority": "high" if finding.get("severity") == "high" else "medium",
            "content": None,
            "expected_sections": [],
            "estimated_tokens": 0,
            "reason": f"{ftype} at {rel}:{line_no} — verified deterministic fix available",
        })
        rank += 1

    return actions, rank


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
    gitignore_seen = False
    for issue in security_issues:
        if issue.get("type") == "exposed_env" and not gitignore_seen:
            gitignore_seen = True
            has_gitignore = scan_result.get("has_gitignore", False)
            existing_gitignore = scan_result.get("gitignore_content", "")
            new_gitignore_content = _generate_gitignore(stack)

            if has_gitignore and existing_gitignore:
                # Merge: keep existing content, append missing env/secret patterns
                env_section = "# Environment and secrets\n.env\n.env.local\n.env.*\n*.pem\n*.key\n"
                if ".env" not in existing_gitignore:
                    merged = existing_gitignore.rstrip("\n") + "\n\n" + env_section
                    action_verb = "update"
                    action_reason = f"{issue.get('file', '.env')} not in .gitignore — appending env/secret patterns"
                else:
                    continue  # gitignore exists and already has .env covered
            else:
                merged = new_gitignore_content
                action_verb = "create"
                action_reason = f"{issue.get('file', '.env')} present but .gitignore missing — secrets at risk"

            security_actions.append({
                "rank": rank,
                "issue_type": "exposed_env",
                "file_to_create": ".gitignore",
                "description": f"Add .env to .gitignore — {issue.get('file', '.env')} is unprotected",
            })
            actions.append({
                "rank": rank,
                "tool": "security",
                "action": action_verb,
                "file": ".gitignore",
                "priority": "high",
                "reason": action_reason,
                "content": merged,
                "expected_sections": [".env"],
                "estimated_tokens": len(merged) // 4,
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

    # Code-level security findings → SECURITY.md audit report (report-only, never auto-fix)
    code_findings = scan_result.get("code_security_findings", [])
    if code_findings:
        high_findings = sum(1 for f in code_findings if f.get("severity") == "high")
        content = _generate_security_md(scan_result)
        security_actions.append({
            "rank": rank,
            "issue_type": "ai_code_patterns",
            "file_to_create": "SECURITY.md",
            "description": f"{len(code_findings)} code-level finding(s) ({high_findings} high) — audit report with fixes",
        })
        actions.append({
            "rank": rank,
            "tool": "security",
            "action": "create",
            "file": "SECURITY.md",
            "priority": "high" if high_findings else "medium",
            "reason": f"{len(code_findings)} security pattern(s) found in source code "
                      f"({high_findings} high severity) — patterns AI assistants commonly introduce",
            "content": content,
            "expected_sections": ["Summary", "Next steps"],
            "estimated_tokens": len(content) // 4,
        })
        rank += 1

    # Code-level remediations (Tier A) — verified in-place fix proposals. Additive to
    # SECURITY.md (which still documents every finding); each is confirmed at the diff gate.
    remediation_actions, rank = _build_remediation_actions(scan_result, rank)
    actions.extend(remediation_actions)

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

    # Prompt library — when AI tools are in use and no PROMPTS.md exists yet
    if detected_tools and not scan_result.get("has_prompts_md"):
        content = _generate_prompts_md(scan_result, conventions)
        actions.append({
            "rank": rank,
            "tool": "workflow",
            "action": "create",
            "file": "PROMPTS.md",
            "priority": "medium",
            "reason": f"AI tools in use ({', '.join(detected_tools)}) but no prompt library — "
                      "starter prompts tailored to the detected stack and conventions",
            "content": content,
            "expected_sections": ["How to use", "Everyday development", "Code quality"],
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
    """Run the Planner agent using Azure AI Foundry Agents API.

    Pure reasoning — no tools. o4-mini analyses the scan + research and generates
    the actual file content for each action. Not a template, not a review.
    """
    agent = client.agents.create_agent(
        model=os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"],
        name="fix-my-vibe-planner",
        instructions=PLANNER_INSTRUCTIONS,
        tools=[],
    )
    # MCP recommendations come from the curated catalogue (deterministic),
    # not from model knowledge — the model only formats them into CLAUDE.md
    mcp_recommendations = recommend_mcp_servers(scan_result.get("detected_stack", []))
    task_message = (
        "Analyse the scan result and research below. "
        "Generate a complete ActionPlan JSON with actual file content for each action. "
        "Base CLAUDE.md on the real readme_summary, commands, and stack found — not a template.\n\n"
        f"{json.dumps({'scan_result': scan_result, 'research': research, 'mcp_recommendations': mcp_recommendations}, indent=2)}"
    )
    thread_id = create_thread_and_send(client, task_message)
    # No tools, but generating full content for 5-6 files in one JSON takes
    # well over 120 polling iterations (observed in_progress at the cap).
    run_agent_with_tools(client, agent.id, thread_id, {}, max_iterations=400)
    raw, reasoning = get_last_assistant_message_with_reasoning(client, thread_id)
    result = parse_json_response(raw)
    result["_reasoning_trace"] = reasoning
    client.agents.delete_agent(agent.id)
    _ensure_security_actions(result, scan_result)
    _normalize_ranks(result)
    return result


def _normalize_ranks(plan: dict) -> None:
    """Guarantee unique, sequential ranks (1..N) over the action list.

    The planner instructions tell o4-mini to give every security action rank 1,
    so its raw output routinely contains duplicate ranks. ``apply_fixes`` keys
    both the confirmation filter and the elicitation checkbox fields (``fix_{rank}``)
    on rank, so duplicates would collapse distinct fixes into a single selectable
    item — you couldn't choose one without the other. Renumber deterministically by
    list position, which preserves the security-first ordering the model produces.
    """
    actions = plan.get("actions", [])
    for i, action in enumerate(actions, start=1):
        action["rank"] = i
    plan["total_actions"] = len(actions)


def _ensure_security_actions(plan: dict, scan_result: dict) -> None:
    """Deterministic backstop: the model plans, Python guarantees the floor.

    o4-mini occasionally drops actions as its instruction load grows (observed
    across runs: one dropped SECURITY.md + .gitignore despite 7 findings and an
    exposed .env; another dropped PROMPTS.md). Security actions are
    non-negotiable and PROMPTS.md is a headline output, so if the parsed plan
    is missing them, inject the locally-generated versions.
    """
    actions = plan.setdefault("actions", [])
    planned_files = {a.get("file") for a in actions}
    injected: list[dict] = []

    has_exposed_env = any(
        i.get("type") == "exposed_env" for i in scan_result.get("security_issues", [])
    )
    if has_exposed_env and ".gitignore" not in planned_files:
        local_plan = run({"scan_result": scan_result, "research": {}})
        gitignore_action = next(
            (a for a in local_plan["actions"] if a["file"] == ".gitignore"), None
        )
        if gitignore_action:
            injected.append(gitignore_action)

    if scan_result.get("code_security_findings") and "SECURITY.md" not in planned_files:
        content = _generate_security_md(scan_result)
        injected.append({
            "tool": "security",
            "action": "create",
            "file": "SECURITY.md",
            "priority": "high",
            "reason": "Code-level security findings present — audit report is mandatory "
                      "(restored by deterministic backstop)",
            "content": content,
            "expected_sections": ["Summary", "Next steps"],
            "estimated_tokens": len(content) // 4,
        })

    appended: list[dict] = []
    if (
        scan_result.get("detected_tools")
        and not scan_result.get("has_prompts_md")
        and "PROMPTS.md" not in planned_files
    ):
        content = _generate_prompts_md(scan_result, scan_result.get("conventions", {}))
        appended.append({
            "tool": "workflow",
            "action": "create",
            "file": "PROMPTS.md",
            "priority": "medium",
            "reason": "AI tools in use but no prompt library — "
                      "(restored by deterministic backstop)",
            "content": content,
            "expected_sections": ["How to use", "Everyday development", "Code quality"],
            "estimated_tokens": len(content) // 4,
        })

    if injected or appended:
        plan["actions"] = injected + actions + appended
        for i, action in enumerate(plan["actions"], start=1):
            action["rank"] = i
        plan["total_actions"] = len(plan["actions"])
        plan["plan_summary"] = (
            plan.get("plan_summary", "")
            + f" [{len(injected) + len(appended)} action(s) restored by deterministic backstop.]"
        ).strip()

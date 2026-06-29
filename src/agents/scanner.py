"""
src/agents/scanner.py
Scanner agent: wraps three-layer detection in a Foundry agent with function calling.
Produces a structured ScanResult that feeds into Researcher and Planner.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.tools.fs_tools import (
    read_existing_context_file,
    infer_project_conventions,
)
from src.tools.security_scan import scan_security_patterns


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
    """Produce the ScanResult — DETERMINISTICALLY, even in Foundry mode.

    Design (see docs/PERFORMANCE-DECISIONS.md, F6): every Scanner "tool" is a
    deterministic local function (detection layers, convention inference, security
    scan) run in a FIXED order — there is no decision for a model to make. Routing
    them through an LLM cost ~43s of round-trips for 0.01s of real work, and the
    round-trip lost exact file/line in the findings (the orchestrator already had
    to re-derive code findings from this same deterministic scan as a result). So
    this calls the local detection path directly; output is complete and exact.

    `client` is accepted for interface symmetry with the other agents but unused —
    this makes no Foundry calls.
    """
    return run({"project_path": str(Path(project_path).resolve())})

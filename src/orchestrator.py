"""
src/orchestrator.py
Main agent orchestration loop for Fix My Vibe.
Coordinates Scanner → Researcher → Planner → [User Confirmation] → Executor → Verifier.
"""

import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _resolve_mode(mode: str) -> str:
    """Resolve the execution mode to a concrete 'local' or 'foundry'.

    'local'/'foundry' pass through; 'auto' picks foundry when
    FOUNDRY_PROJECT_ENDPOINT is set, else local — mirroring cli.py.
    """
    if mode in ("local", "foundry"):
        return mode
    return "foundry" if os.environ.get("FOUNDRY_PROJECT_ENDPOINT") else "local"


def run_plan_phase(project_path: str, mode: str = "auto", verbose: bool = False) -> dict:
    """Run Scanner → Researcher → Planner. NEVER writes files.

    Returns {"mode", "scan_result", "research_result", "plan_result"} or
    {"error": ...} if the path is missing. Shared by the CLI and the MCP server
    so both front-ends produce identical diagnoses and plans.

    Foundry mode builds the AIProjectClient once and falls back to local if the
    client can't be created (mirrors the old run_with_foundry behavior).
    """
    from src.agents import scanner, researcher, planner

    path = Path(project_path).resolve()
    if not path.exists():
        return {"error": f"Path does not exist: {project_path}"}

    resolved = _resolve_mode(mode)
    client = None
    if resolved == "foundry":
        from src.foundry_utils import get_client
        try:
            client = get_client()
        except Exception as e:
            print(f"  Foundry unavailable ({e}) — falling back to local mode")
            resolved = "local"

    if resolved == "foundry":
        scan_result = scanner.run_with_foundry(client, str(path))
        if verbose:
            _print_reasoning_trace(scan_result, "Scanner")
        research_result = researcher.run_with_foundry(client, scan_result)
        if verbose:
            _print_reasoning_trace(research_result, "Researcher")
        plan_result = planner.run_with_foundry(client, scan_result, research_result)
        if verbose:
            _print_reasoning_trace(plan_result, "Planner")
    else:
        scan_result = scanner.run({"project_path": str(path)})
        if verbose:
            print(json.dumps(scan_result, indent=2))
        research_result = researcher.run({
            "detected_tools": scan_result.get("detected_tools", []),
            "detected_stack": scan_result.get("detected_stack", []),
        })
        plan_result = planner.run({
            "scan_result": scan_result,
            "research": research_result,
        })

    return {
        "mode": resolved,
        "scan_result": scan_result,
        "research_result": research_result,
        "plan_result": plan_result,
    }


def run_apply_phase(
    project_path: str,
    plan_result: dict,
    confirmed_ranks: list[int],
    mode: str = "auto",
    verbose: bool = False,
) -> dict:
    """Run Executor (for confirmed_ranks) → Verifier. The ONLY code path that writes.

    Confirmation must already have happened upstream; confirmed_ranks is the
    pre-validated list of action ranks to write. Returns
    {"execution_result", "verify_result"}.
    """
    from src.agents import executor, verifier

    path = Path(project_path).resolve()
    resolved = _resolve_mode(mode)
    client = None
    if resolved == "foundry":
        from src.foundry_utils import get_client
        try:
            client = get_client()
        except Exception as e:
            print(f"  Foundry unavailable ({e}) — falling back to local mode")
            resolved = "local"

    if resolved == "foundry":
        execution_result = executor.run_with_foundry(
            client, str(path), plan_result, confirmed_ranks
        )
        if verbose:
            _print_reasoning_trace(execution_result, "Executor")
        verify_result = verifier.run_with_foundry(
            client, str(path), execution_result, plan_result
        )
        if verbose:
            _print_reasoning_trace(verify_result, "Verifier")
    else:
        execution_result = executor.run(
            {"project_path": str(path), "action_plan": plan_result},
            confirm_fn=lambda _plan: confirmed_ranks,
        )
        verify_result = verifier.run({
            "project_path": str(path),
            "execution_result": execution_result,
            "action_plan": plan_result,
        })

    return {
        "execution_result": execution_result,
        "verify_result": verify_result,
    }


def run_local(project_path: str, confirm_fn=None, verbose: bool = False) -> dict:
    """
    Run the full Fix My Vibe pipeline in local mode (no Azure Foundry).
    Delegates to the shared run_plan_phase / run_apply_phase functions.
    """
    return _run_pipeline(project_path, confirm_fn=confirm_fn, verbose=verbose, mode="local")


def run_with_foundry(project_path: str, confirm_fn=None, verbose: bool = False) -> dict:
    """
    Run the full Fix My Vibe pipeline using Azure AI Foundry agents.
    Falls back to local mode if Foundry is unavailable.
    """
    return _run_pipeline(project_path, confirm_fn=confirm_fn, verbose=verbose, mode="foundry")


def _run_pipeline(project_path: str, confirm_fn=None, verbose: bool = False, mode: str = "auto") -> dict:
    """Shared CLI pipeline: plan → confirm → apply → verify, with terminal output."""
    path = Path(project_path).resolve()
    if not path.exists():
        return {"error": f"Path does not exist: {project_path}"}

    label = "Foundry mode" if _resolve_mode(mode) == "foundry" else "local mode"
    print(f"\n  Fix My Vibe ({label}) — scanning: {path}\n")

    # Phases 1-3: Scanner → Researcher → Planner (no writes)
    print("[ 1/2 ] Diagnosing project (scan → research → plan)...")
    plan_phase = run_plan_phase(str(path), mode=mode, verbose=verbose)
    if "error" in plan_phase:
        return plan_phase

    scan_result = plan_phase["scan_result"]
    research_result = plan_phase["research_result"]
    plan_result = plan_phase["plan_result"]
    resolved_mode = plan_phase["mode"]

    _print_scan_summary(scan_result)

    if not plan_result.get("actions"):
        print("\n  Nothing to fix — your AI tool setup looks good!")
        return {"status": "nothing_to_do", "scan_result": scan_result}

    # Confirmation gate — runs locally (user must approve)
    print("\n[ 2/2 ] Executor agent — preparing to write files...")
    if confirm_fn is None:
        from src.agents.executor import _confirm_actions
        confirmed_ranks = _confirm_actions(plan_result)
    else:
        confirmed_ranks = confirm_fn(plan_result)

    if not confirmed_ranks:
        return {"status": "cancelled", "scan_result": scan_result, "plan_result": plan_result}

    # Apply phase: Executor → Verifier (writes) — reuse the resolved mode so we
    # don't re-decide foundry vs local between phases.
    apply_phase = run_apply_phase(
        str(path), plan_result, confirmed_ranks, mode=resolved_mode, verbose=verbose
    )
    execution_result = apply_phase["execution_result"]
    verify_result = apply_phase["verify_result"]

    if not execution_result.get("executed"):
        print(f"\n  {execution_result.get('summary', 'No files written.')}")
        return {
            "status": "cancelled",
            "scan_result": scan_result,
            "plan_result": plan_result,
        }

    print(f"\n  {execution_result.get('summary', '')}")
    _print_verification_summary(verify_result)

    return {
        "status": "completed",
        "scan_result": scan_result,
        "research_result": research_result,
        "plan_result": plan_result,
        "execution_result": execution_result,
        "verify_result": verify_result,
    }


def _print_reasoning_trace(result: dict, agent_name: str) -> None:
    trace = result.pop("_reasoning_trace", None)
    if trace:
        print(f"\n  ── {agent_name} Reasoning ──────────────────────────────────────")
        for line in trace.splitlines():
            print(f"  {line}")
        print("  ────────────────────────────────────────────────────────────────\n")


def _print_scan_summary(scan_result: dict) -> None:
    tools = scan_result.get("detected_tools", [])
    stack = scan_result.get("detected_stack", [])
    sec = scan_result.get("security_issues", [])
    missing = scan_result.get("missing_configs", {})

    print(f"  Tools detected: {', '.join(tools) if tools else 'none'}")
    print(f"  Stack: {', '.join(stack) if stack else 'unknown'}")
    if sec:
        print(f"  Security issues: {len(sec)} found")
    if missing:
        print(f"  Missing configs: {', '.join(missing.keys())}")


def _print_verification_summary(verify_result: dict) -> None:
    print(f"  {verify_result.get('summary', '')}")
    for rec in verify_result.get("recommendations", []):
        print(f"  Recommendation: {rec}")

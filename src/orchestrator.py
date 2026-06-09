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


def run_local(project_path: str, confirm_fn=None, verbose: bool = False) -> dict:
    """
    Run the full Fix My Vibe pipeline in local mode (no Azure Foundry).
    Uses local Python logic for all agents.
    """
    from src.agents import scanner, researcher, planner, executor, verifier

    path = Path(project_path).resolve()
    if not path.exists():
        return {"error": f"Path does not exist: {project_path}"}

    print(f"\n  Fix My Vibe — scanning: {path}\n")

    # 1. Scanner
    print("[ 1/5 ] Scanner agent running...")
    scan_result = scanner.run({"project_path": str(path)})
    if verbose:
        print(json.dumps(scan_result, indent=2))

    _print_scan_summary(scan_result)

    # 2. Researcher
    print("\n[ 2/5 ] Researcher agent running...")
    research_result = researcher.run({
        "detected_tools": scan_result.get("detected_tools", []),
        "detected_stack": scan_result.get("detected_stack", []),
    })

    # 3. Planner
    print("\n[ 3/5 ] Planner agent reasoning...")
    plan_result = planner.run({
        "scan_result": scan_result,
        "research": research_result,
    })

    if not plan_result.get("actions"):
        print("\n  Nothing to fix — your AI tool setup looks good!")
        return {"status": "nothing_to_do", "scan_result": scan_result}

    # 4. Executor (with confirmation gate)
    print("\n[ 4/5 ] Executor agent — preparing to write files...")
    execution_result = executor.run({
        "project_path": str(path),
        "action_plan": plan_result,
    }, confirm_fn=confirm_fn)

    if not execution_result.get("executed"):
        print(f"\n  {execution_result.get('summary', 'No files written.')}")
        return {
            "status": "cancelled",
            "scan_result": scan_result,
            "plan_result": plan_result,
        }

    print(f"\n  {execution_result['summary']}")

    # 5. Verifier
    print("\n[ 5/5 ] Verifier agent checking outputs...")
    verify_result = verifier.run({
        "project_path": str(path),
        "execution_result": execution_result,
        "action_plan": plan_result,
    })

    _print_verification_summary(verify_result)

    return {
        "status": "completed",
        "scan_result": scan_result,
        "research_result": research_result,
        "plan_result": plan_result,
        "execution_result": execution_result,
        "verify_result": verify_result,
    }


def run_with_foundry(project_path: str, confirm_fn=None, verbose: bool = False) -> dict:
    """
    Run the full Fix My Vibe pipeline using Azure AI Foundry agents.
    Falls back to local mode if Foundry is unavailable.
    """
    from src.foundry_utils import get_client
    from src.agents import scanner, researcher, planner, executor, verifier

    try:
        client = get_client()
    except Exception as e:
        print(f"  Foundry unavailable ({e}) — falling back to local mode")
        return run_local(project_path, confirm_fn=confirm_fn, verbose=verbose)

    path = Path(project_path).resolve()
    print(f"\n  Fix My Vibe (Foundry mode) — scanning: {path}\n")

    print("[ 1/5 ] Scanner agent running (Foundry)...")
    scan_result = scanner.run_with_foundry(client, str(path))
    if verbose:
        _print_reasoning_trace(scan_result, "Scanner")
    _print_scan_summary(scan_result)

    print("\n[ 2/5 ] Researcher agent running (Bing Grounding)...")
    research_result = researcher.run_with_foundry(client, scan_result)
    if verbose:
        _print_reasoning_trace(research_result, "Researcher")

    print("\n[ 3/5 ] Planner agent reasoning (Foundry)...")
    plan_result = planner.run_with_foundry(client, scan_result, research_result)
    if verbose:
        _print_reasoning_trace(plan_result, "Planner")

    if not plan_result.get("actions"):
        print("\n  Nothing to fix — your AI tool setup looks good!")
        return {"status": "nothing_to_do", "scan_result": scan_result}

    # Confirmation gate — runs locally (user must approve)
    print("\n[ 4/5 ] Executor agent — preparing to write files...")
    if confirm_fn is None:
        from src.agents.executor import _confirm_actions
        confirmed_ranks = _confirm_actions(plan_result)
    else:
        confirmed_ranks = confirm_fn(plan_result)

    if not confirmed_ranks:
        return {"status": "cancelled", "scan_result": scan_result, "plan_result": plan_result}

    execution_result = executor.run_with_foundry(client, str(path), plan_result, confirmed_ranks)
    if verbose:
        _print_reasoning_trace(execution_result, "Executor")
    print(f"\n  {execution_result.get('summary', '')}")

    print("\n[ 5/5 ] Verifier agent checking outputs (Foundry)...")
    verify_result = verifier.run_with_foundry(client, str(path), execution_result, plan_result)
    if verbose:
        _print_reasoning_trace(verify_result, "Verifier")
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

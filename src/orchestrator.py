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
        _augment_with_remediations(plan_result, scan_result, client)
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


def _augment_with_remediations(plan_result: dict, scan_result: dict, client) -> None:
    """Append code-remediation actions to a Foundry plan: Tier-A deterministic
    (mode-independent) + Tier-B/C KB-grounded (LLM). Re-normalizes ranks so the
    elicitation checkboxes stay 1..N. Defensive: a remediator failure logs and
    leaves the plan otherwise intact (config fixes + SECURITY.md still stand).

    Local mode already adds Tier A inside planner.run, so this is Foundry-only.
    """
    from src.agents import planner, remediator
    from src.tools.security_scan import scan_security_patterns

    actions = plan_result.setdefault("actions", [])
    next_rank = len(actions) + 1

    # Code findings are DETERMINISTIC facts (D8): in Foundry mode scan_result has been
    # round-tripped through the LLM, which doesn't reliably preserve exact file/line —
    # and remediation needs those to locate the code. Re-derive from the scanner so
    # both tiers work off ground truth, not the model's paraphrase.
    project_path = scan_result.get("project_path", "")
    if project_path:
        det = scan_security_patterns(project_path)
        if "findings" in det:
            scan_result = {**scan_result, "code_security_findings": det["findings"]}

    tier_a, next_rank = planner._build_remediation_actions(scan_result, next_rank)
    actions.extend(tier_a)

    try:
        tier_bc, next_rank = remediator.run_with_foundry(client, scan_result, next_rank)
        actions.extend(tier_bc)
    except Exception as e:
        print(f"  Remediator unavailable ({e}) — Tier-B/C findings remain in SECURITY.md")

    planner._normalize_ranks(plan_result)


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
        # Remediations are deterministic, safety-gated line edits — apply + verify
        # them via the local harness in EVERY mode. The Foundry executor (LLM-driven
        # write_file) only handles config-file writes; it can't do apply_code_fix and
        # would otherwise silently drop content-less remediate actions.
        remediate_ranks = [
            a["rank"] for a in plan_result.get("actions", [])
            if a.get("action") == "remediate" and a.get("rank") in confirmed_ranks
        ]
        config_ranks = [r for r in confirmed_ranks if r not in remediate_ranks]

        execution_result = executor.run_with_foundry(
            client, str(path), plan_result, config_ranks
        )
        if verbose:
            _print_reasoning_trace(execution_result, "Executor")
        verify_result = verifier.run_with_foundry(
            client, str(path), execution_result, plan_result
        )
        if verbose:
            _print_reasoning_trace(verify_result, "Verifier")

        if remediate_ranks:
            _apply_remediations_deterministically(
                str(path), plan_result, remediate_ranks, execution_result, verify_result
            )
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


def _apply_remediations_deterministically(
    path: str, plan_result: dict, ranks: list[int],
    execution_result: dict, verify_result: dict,
) -> None:
    """Apply + verify the remediate actions in `ranks` via the local deterministic
    harness, merging results into the (Foundry) execution_result / verify_result in
    place. Reuses the same code-fix + scan_file verification the local path uses, so
    remediation behaves identically regardless of mode.
    """
    from src.agents import executor, verifier

    rem_exec = executor.run(
        {"project_path": path, "action_plan": plan_result},
        confirm_fn=lambda _plan: ranks,
    )
    rem_verify = verifier.run({
        "project_path": path,
        "execution_result": rem_exec,
        "action_plan": plan_result,
    })

    execution_result.setdefault("executed", []).extend(rem_exec.get("executed", []))
    execution_result.setdefault("errors", []).extend(rem_exec.get("errors", []))
    written = len(execution_result["executed"])
    errs = len(execution_result["errors"])
    execution_result["summary"] = f"{written} change(s) applied. {errs} error(s)."

    vresults = verify_result.setdefault("verification_results", [])
    vresults.extend(rem_verify.get("verification_results", []))
    verify_result.setdefault("recommendations", []).extend(rem_verify.get("recommendations", []))
    passed = sum(1 for r in vresults if r.get("status") == "pass")
    total = len(vresults)
    verify_result["overall_pass"] = passed == total and total > 0
    verify_result["summary"] = f"{passed}/{total} checks passed."


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

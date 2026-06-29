"""
src/agents/executor.py
Executor agent: presents the ActionPlan to the user, requires explicit confirmation,
then writes each file with backup. Never writes without confirmation.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.tools.fs_tools import write_file


def _display_plan(action_plan: dict) -> None:
    """Pretty-print the action plan to the terminal."""
    actions = action_plan.get("actions", [])
    print("\n" + "=" * 60)
    print("  FIX MY VIBE — Action Plan")
    print("=" * 60)
    print(f"\n{action_plan.get('plan_summary', '')}")
    print(f"Convention: {action_plan.get('convention_summary', '')}\n")

    for action in actions:
        priority = action.get("priority", "medium").upper()
        priority_icon = "🔴" if priority == "HIGH" else "🟡"
        action_type = action.get("action", "create").upper()
        print(f"  [{action.get('rank', '?')}] {priority_icon} {priority} — {action_type} {action.get('file', '')}")
        print(f"      {action.get('reason', '')}")
        if action.get("action") == "remediate":
            # Show the exact diff — code edits are confirmed on the change itself.
            if action.get("rationale"):
                print(f"      Why: {action['rationale']}")
            from src.tools.remediation import render_diff
            patch = action.get("patch", "")
            if patch:
                print(render_diff(patch))
            for cite in action.get("kb_citations", [])[:2]:
                if cite.get("title"):
                    print(f"      ↳ {cite['title']}")
            if action.get("requires_followup"):
                print(f"      ⚠ Follow-up required: {action['requires_followup']}")
        elif action.get("content"):
            tokens = action.get("estimated_tokens", 0)
            print(f"      Content: {tokens} tokens")
        print()

    print("=" * 60)


def _confirm_actions(action_plan: dict) -> list[int]:
    """
    Interactive confirmation gate. Returns list of confirmed action ranks.
    This is NEVER skipped — even in test mode, this must be called with mock input.
    """
    actions = action_plan.get("actions", [])
    if not actions:
        return []

    _display_plan(action_plan)

    print("\nOptions:")
    print("  [a] Apply ALL actions")
    print("  [s] Select specific actions")
    print("  [n] Cancel — apply nothing")
    print()

    while True:
        choice = input("Your choice [a/s/n]: ").strip().lower()

        if choice == "n":
            print("Cancelled. No files written.")
            return []

        if choice == "a":
            return [a["rank"] for a in actions]

        if choice == "s":
            print(f"Enter action numbers separated by commas (e.g. 1,2,3):")
            nums_input = input("Actions: ").strip()
            try:
                selected = [int(x.strip()) for x in nums_input.split(",") if x.strip()]
                valid = [r for r in selected if any(a["rank"] == r for a in actions)]
                if valid:
                    return valid
                else:
                    print("No valid action numbers entered. Try again.")
            except ValueError:
                print("Invalid input. Try again.")

        print("Please enter 'a', 's', or 'n'.")


def run(input: dict, confirm_fn=None) -> dict:
    """
    Standalone interface: execute the action plan.
    confirm_fn: callable(action_plan) -> list[int] of confirmed ranks.
    Defaults to interactive terminal prompt.
    """
    project_path = input.get("project_path", "")
    action_plan = input.get("action_plan", {})
    actions = action_plan.get("actions", [])

    if not actions:
        return {
            "executed": [],
            "skipped": [],
            "errors": [],
            "summary": "No actions to execute.",
        }

    # Confirmation gate — non-optional
    if confirm_fn is None:
        confirmed_ranks = _confirm_actions(action_plan)
    else:
        confirmed_ranks = confirm_fn(action_plan)

    if not confirmed_ranks:
        return {
            "executed": [],
            "skipped": [{"rank": a["rank"], "file": a["file"], "reason": "user cancelled"} for a in actions],
            "errors": [],
            "summary": "No files written — user cancelled.",
        }

    executed: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []

    for action in sorted(actions, key=lambda a: a.get("rank", 999)):
        rank = action.get("rank")
        file_path = action.get("file", "")
        content = action.get("content")

        if rank not in confirmed_ranks:
            skipped.append({"rank": rank, "file": file_path, "reason": "not selected"})
            continue

        # Code remediation: a targeted in-place edit, not a whole-file write.
        # Handled before the content-None skip below — remediate actions carry no
        # `content` field by design, so they would otherwise be dropped as "manual".
        if action.get("action") == "remediate":
            from src.tools.remediation import apply_code_fix
            result = apply_code_fix(
                project_path,
                file_path,
                action.get("line"),
                action.get("expected_line", ""),
                action.get("proposed_line", ""),
                action.get("add_imports", ()),
            )
            if "error" in result:
                errors.append({"rank": rank, "file": file_path, "error": result["error"]})
                print(f"  ✗ ERROR fixing {file_path}: {result['error']}")
            else:
                executed.append({
                    "rank": rank,
                    "file": file_path,
                    "status": "remediated",
                    "backed_up": result.get("backed_up", False),
                    "backup_path": result.get("backup_path"),
                    "line": action.get("line"),
                })
                print(f"  ✓ Fixed: {file_path}:{action.get('line')} (backup created)")
            continue

        if action.get("action") == "improve" or content is None:
            skipped.append({
                "rank": rank,
                "file": file_path,
                "reason": "improve action — manual edit required",
            })
            continue

        result = write_file(project_path, file_path, content)
        if "error" in result:
            errors.append({"rank": rank, "file": file_path, "error": result["error"]})
            print(f"  ✗ ERROR writing {file_path}: {result['error']}")
        else:
            executed.append({
                "rank": rank,
                "file": file_path,
                "status": "written",
                "backed_up": result.get("backed_up", False),
                "size_bytes": result.get("size_bytes", 0),
            })
            backup_note = " (backup created)" if result.get("backed_up") else ""
            print(f"  ✓ Written: {file_path}{backup_note}")

    written = len(executed)
    backed_up = sum(1 for e in executed if e.get("backed_up"))
    err_count = len(errors)
    summary = f"Wrote {written} file(s). {backed_up} backed up. {err_count} error(s)."

    return {
        "executed": executed,
        "skipped": skipped,
        "errors": errors,
        "summary": summary,
    }


def run_with_foundry(client, project_path: str, action_plan: dict, confirmed_ranks: list[int]) -> dict:
    """Execute confirmed config-file writes — DETERMINISTICALLY, even in Foundry mode.

    Design (see docs/PERFORMANCE-DECISIONS.md, D-P2): the plan already carries the
    exact, finalised `content` for each action (the deterministic Planner produced
    it). Routing that content through an LLM that calls write_file added ~116s of
    round-trips AND corrupted the output — the model mangled em-dashes (`—`) into
    control char `\\x14`. Writing a finalised file to disk involves no reasoning, so
    the LLM was pure cost and risk. This now uses the same deterministic path as
    local mode; the executed/errors ledger is therefore ground truth by construction.

    `client` is accepted for interface symmetry with the other agents but unused —
    this makes no Foundry calls. Remediate actions are handled separately by the
    orchestrator (_apply_remediations_deterministically); this writes the confirmed
    content/config actions only.
    """
    return run(
        {"project_path": project_path, "action_plan": action_plan},
        confirm_fn=lambda _plan: confirmed_ranks,
    )

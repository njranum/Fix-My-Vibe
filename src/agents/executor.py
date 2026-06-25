"""
src/agents/executor.py
Executor agent: presents the ActionPlan to the user, requires explicit confirmation,
then writes each file with backup. Never writes without confirmation.
"""

import os
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.tools.fs_tools import write_file
from src.foundry_utils import (
    get_last_assistant_message_with_reasoning,
    parse_json_response,
    create_thread_and_send,
    run_agent_with_tools,
)


EXECUTOR_INSTRUCTIONS = """
You are the Executor agent for Fix My Vibe. Your role is to execute approved actions
from the ActionPlan. You ONLY write files — you never modify the plan or add new actions.

You will receive:
- action_plan: the approved plan from the Planner
- project_path: where to write files
- confirmed_actions: list of action ranks that the user has confirmed

For each confirmed action (in rank order):
1. Call write_file with the exact content from the action plan
2. Report the result

Your output must be valid JSON:
{
  "executed": [
    {
      "rank": 1,
      "file": ".gitignore",
      "status": "written",
      "backed_up": true,
      "size_bytes": 123
    }
  ],
  "skipped": [],
  "errors": [],
  "summary": "Wrote 2 files. 1 backed up. 0 errors."
}

Return ONLY the JSON — no markdown fences, no preamble.
"""


def _get_tool_definitions() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write a file to the project directory (with automatic backup if it exists)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_path": {"type": "string"},
                        "relative_path": {"type": "string", "description": "Path relative to project_path"},
                        "content": {"type": "string", "description": "Full file content to write"},
                    },
                    "required": ["project_path", "relative_path", "content"],
                },
            },
        }
    ]


def _make_tool_handlers(project_path: str, write_log: list[dict] | None = None) -> dict:
    """write_log, when provided, records every actual write — ground truth for
    the execution result, independent of what the model reports afterwards."""
    def _handle_write(args: dict) -> dict:
        result = write_file(
            project_path,  # always use the pre-validated path, not what the model passes
            args["relative_path"],
            args["content"],
        )
        if write_log is not None:
            if "error" in result:
                write_log.append({"file": args["relative_path"], "error": result["error"]})
                print(f"  ✗ ERROR writing {args['relative_path']}: {result['error']}")
            else:
                write_log.append({
                    "file": args["relative_path"],
                    "status": "written",
                    "backed_up": result.get("backed_up", False),
                    "size_bytes": result.get("size_bytes", 0),
                })
                backup_note = " (backup created)" if result.get("backed_up") else ""
                print(f"  ✓ Written: {args['relative_path']}{backup_note}")
        return result

    return {"write_file": _handle_write}


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
            patch = action.get("patch", "")
            for pline in patch.splitlines():
                if pline.startswith(("+++", "---", "@@")):
                    continue
                if pline.startswith("+"):
                    print(f"        + {pline[1:].strip()}")
                elif pline.startswith("-"):
                    print(f"        - {pline[1:].strip()}")
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
    """Run the Executor agent using Azure AI Foundry Agents API.

    Confirmation gate already ran in Python (confirmed_ranks is pre-validated).
    o4-mini calls write_file for each confirmed action — actual file I/O happens
    when the model's tool calls are executed locally.
    """
    abs_path = str(Path(project_path).resolve())
    actions = action_plan.get("actions", [])
    confirmed_actions = [
        a for a in actions
        if a.get("rank") in confirmed_ranks
        and a.get("action") != "improve"
        and a.get("content") is not None
    ]

    if not confirmed_actions:
        return {
            "executed": [],
            "skipped": [{"rank": a["rank"], "file": a["file"], "reason": "user cancelled"} for a in actions],
            "errors": [],
            "summary": "No files written — user cancelled.",
        }

    agent = client.agents.create_agent(
        model=os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"],
        name="fix-my-vibe-executor",
        instructions=EXECUTOR_INSTRUCTIONS,
        tools=_get_tool_definitions(),
    )
    task_message = (
        f"Execute the following confirmed actions for the project at {abs_path}. "
        "Call write_file for each action using the exact content provided — do not modify it. "
        "Return an ExecutionResult JSON.\n\n"
        f"Confirmed actions:\n{json.dumps(confirmed_actions, indent=2)}"
    )
    write_log: list[dict] = []
    thread_id = create_thread_and_send(client, task_message)
    # Higher iteration budget: one write_file round-trip per action plus polling
    # adds up — the default 120 was observed running out mid-plan (6 actions).
    run_agent_with_tools(
        client, agent.id, thread_id,
        _make_tool_handlers(abs_path, write_log),
        max_iterations=400,
    )
    raw, reasoning = get_last_assistant_message_with_reasoning(client, thread_id)
    result = parse_json_response(raw)
    result["_reasoning_trace"] = reasoning
    client.agents.delete_agent(agent.id)

    # Ground truth override: executed/errors come from the write_file ledger,
    # not the model's self-report (observed: model wrote 5 files, reported 0).
    rank_by_file = {a.get("file"): a.get("rank") for a in confirmed_actions}
    executed = [
        {**e, "rank": rank_by_file.get(e["file"])}
        for e in write_log if e.get("status") == "written"
    ]
    errors = [e for e in write_log if "error" in e]
    backed_up = sum(1 for e in executed if e.get("backed_up"))
    result["executed"] = executed
    result["errors"] = errors
    result.setdefault("skipped", [])
    result["summary"] = f"Wrote {len(executed)} file(s). {backed_up} backed up. {len(errors)} error(s)."
    return result

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
        if action.get("content"):
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
    """Run the Executor agent using Azure AI Foundry.

    File writes happen locally — this is safety-critical and must be deterministic.
    Phi-4-reasoning is used only to produce a natural-language summary of what was done,
    which becomes the _reasoning_trace visible in --verbose mode.
    """
    # Execute locally — confirmed_ranks already passed so no gate needed here
    confirm_fn = lambda plan: confirmed_ranks
    result = run(
        {"project_path": project_path, "action_plan": action_plan},
        confirm_fn=confirm_fn,
    )

    # Ask Phi-4 to narrate what happened — useful trace for --verbose
    executed = result.get("executed", [])
    errors = result.get("errors", [])

    reasoning_prompt = (
        f"You are a senior developer. Files were just written to a project. "
        f"Narrate what happened in 2-3 sentences for a developer log.\n\n"
        f"Written files: {json.dumps([e['file'] for e in executed])}\n"
        f"Errors: {json.dumps(errors)}\n"
        f"Backups created: {sum(1 for e in executed if e.get('backed_up'))} file(s)\n\n"
        f"Be specific about what each file does for the developer's AI tool setup."
    )

    try:
        model = os.environ.get("FOUNDRY_MODEL_DEPLOYMENT_NAME", "Phi-4-reasoning")
        chat = client.inference.get_chat_completions_client()
        response = chat.complete(
            model=model,
            messages=[{"role": "user", "content": reasoning_prompt}],
        )
        result["_reasoning_trace"] = response.choices[0].message.content
    except Exception:
        pass  # reasoning trace is optional — don't fail the execution

    return result

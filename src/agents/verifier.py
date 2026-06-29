"""
src/agents/verifier.py
Verifier agent: checks that written files contain expected sections
and meet quality thresholds. Final gate before reporting success.
"""

import os
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.tools.fs_tools import verify_file, read_existing_context_file
from src.foundry_utils import (
    get_last_assistant_message_with_reasoning,
    parse_json_response,
    create_thread_and_send,
    run_agent_with_tools,
)


VERIFIER_INSTRUCTIONS = """
You are the Verifier agent for Fix My Vibe. You validate that written config files
actually contain what they should, and report quality issues.

You will receive:
- execution_result: which files were written and their expected_sections
- project_path: where the files live

For each written file:
1. Call verify_file(project_path, file_path, expected_sections)
2. Call read_existing_context_file(project_path, file_path) for quality audit
3. Report pass/fail with specifics

Output JSON:
{
  "verification_results": [
    {
      "file": "CLAUDE.md",
      "verified": true,
      "missing_sections": [],
      "quality_concerns": [],
      "size_bytes": 1234,
      "status": "pass"
    },
    {
      "file": ".cursorrules",
      "verified": false,
      "missing_sections": ["Testing approach"],
      "quality_concerns": ["Very short — likely missing key sections"],
      "size_bytes": 80,
      "status": "fail"
    }
  ],
  "overall_pass": true,
  "summary": "2/2 files verified. All sections present.",
  "recommendations": []
}

Return ONLY the JSON — no markdown fences, no preamble.
"""


def _get_tool_definitions() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "verify_file",
                "description": "Check that a file exists and contains expected sections",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_path": {"type": "string"},
                        "relative_path": {"type": "string"},
                        "expected_sections": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["project_path", "relative_path", "expected_sections"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_existing_context_file",
                "description": "Read and audit a context file for quality metrics",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_path": {"type": "string"},
                        "filename": {"type": "string"},
                    },
                    "required": ["project_path", "filename"],
                },
            },
        },
    ]


def _make_tool_handlers(project_path: str) -> dict:
    # The model occasionally emits a malformed tool call with missing arguments
    # (observed: verify_file({})). A bare args["relative_path"] then raises
    # KeyError inside the polling loop and aborts the whole pipeline AFTER files
    # were already written. Return a structured error instead so the run survives
    # and the model can correct itself on the next turn.
    def _verify_file(args: dict):
        rel = args.get("relative_path")
        if not rel:
            return {"error": "verify_file requires 'relative_path'"}
        return verify_file(project_path, rel, args.get("expected_sections", []))

    def _read_context(args: dict):
        filename = args.get("filename")
        if not filename:
            return {"error": "read_existing_context_file requires 'filename'"}
        return read_existing_context_file(project_path, filename)

    return {
        "verify_file": _verify_file,
        "read_existing_context_file": _read_context,
    }


def run(input: dict) -> dict:
    """Standalone interface: verify written files without Foundry."""
    project_path = input.get("project_path", "")
    execution_result = input.get("execution_result", {})
    action_plan = input.get("action_plan", {})

    executed = execution_result.get("executed", [])
    actions = action_plan.get("actions", [])
    actions_by_file = {a.get("file"): a for a in actions}
    # Remediations can target several lines in one file, so key them by (file, line).
    remediation_by_loc = {
        (a.get("file"), a.get("line")): a
        for a in actions if a.get("action") == "remediate"
    }

    verification_results: list[dict] = []
    for item in executed:
        file_path = item.get("file", "")

        # Code remediation: verify by re-scanning the single file and confirming the
        # specific finding is gone at the edited line — not a section check.
        if item.get("status") == "remediated":
            from src.tools.security_scan import scan_file
            action = remediation_by_loc.get((file_path, item.get("line")), {})
            ftype = action.get("finding_type")
            scan = scan_file(str(Path(project_path) / file_path))
            remaining = [
                f for f in scan.get("findings", [])
                if f.get("type") == ftype and f.get("line") == item.get("line")
            ]
            verified = "error" not in scan and not remaining
            verification_results.append({
                "file": file_path,
                "verified": verified,
                "missing_sections": [],
                "quality_concerns": [] if verified else [f"{ftype} still present after fix"],
                "size_bytes": 0,
                "status": "pass" if verified else "fail",
            })
            continue

        action = actions_by_file.get(file_path, {})
        expected_sections = action.get("expected_sections", [])

        verify_result = verify_file(project_path, file_path, expected_sections)
        audit = read_existing_context_file(project_path, file_path)

        status = "pass" if verify_result.get("verified") else "fail"
        verification_results.append({
            "file": file_path,
            "verified": verify_result.get("verified", False),
            "missing_sections": verify_result.get("missing_sections", []),
            "quality_concerns": audit.get("quality_concerns", []) if audit.get("exists") else [],
            "size_bytes": verify_result.get("size_bytes", 0),
            "status": status,
        })

    passed = sum(1 for r in verification_results if r["status"] == "pass")
    total = len(verification_results)
    overall_pass = passed == total and total > 0

    recommendations: list[str] = []
    for r in verification_results:
        if r["missing_sections"]:
            recommendations.append(
                f"Add these sections to {r['file']}: {', '.join(r['missing_sections'])}"
            )
        for concern in r.get("quality_concerns", []):
            recommendations.append(f"{r['file']}: {concern}")

    return {
        "verification_results": verification_results,
        "overall_pass": overall_pass,
        "summary": f"{passed}/{total} files verified." + (" All sections present." if overall_pass else " Some issues found."),
        "recommendations": recommendations,
    }


def run_with_foundry(client, project_path: str, execution_result: dict, action_plan: dict) -> dict:
    """Run the Verifier agent using Azure AI Foundry Agents API.

    o4-mini decides what to check and makes qualitative judgments about file quality.
    Python executes the verify_file and read_existing_context_file calls.
    """
    abs_path = str(Path(project_path).resolve())
    executed = execution_result.get("executed", [])
    actions_by_file = {a.get("file"): a for a in action_plan.get("actions", [])}
    verification_tasks = [
        {
            "file": item["file"],
            "expected_sections": actions_by_file.get(item["file"], {}).get("expected_sections", []),
        }
        for item in executed
    ]

    agent = client.agents.create_agent(
        model=os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"],
        name="fix-my-vibe-verifier",
        instructions=VERIFIER_INSTRUCTIONS,
        tools=_get_tool_definitions(),
    )
    task_message = (
        f"Verify the files written to {abs_path}. "
        "For each file: call verify_file to check existence and sections, "
        "then call read_existing_context_file to assess content quality. "
        "Make qualitative judgments — flag generic boilerplate even if sections are present. "
        "Return a VerificationResult JSON.\n\n"
        f"Files to verify:\n{json.dumps(verification_tasks, indent=2)}"
    )
    thread_id = create_thread_and_send(client, task_message)
    # Two tool round-trips per file plus polling — default 120 is too tight
    # for plans with 5-6 files (same failure mode as the executor).
    run_agent_with_tools(
        client, agent.id, thread_id, _make_tool_handlers(abs_path), max_iterations=400
    )
    raw, reasoning = get_last_assistant_message_with_reasoning(client, thread_id)
    result = parse_json_response(raw)
    result["_reasoning_trace"] = reasoning
    client.agents.delete_agent(agent.id)
    return result

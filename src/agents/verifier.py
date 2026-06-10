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


def run(input: dict) -> dict:
    """Standalone interface: verify written files without Foundry."""
    project_path = input.get("project_path", "")
    execution_result = input.get("execution_result", {})
    action_plan = input.get("action_plan", {})

    executed = execution_result.get("executed", [])
    actions_by_file = {
        a.get("file"): a
        for a in action_plan.get("actions", [])
    }

    verification_results: list[dict] = []
    for item in executed:
        file_path = item.get("file", "")
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
    """Run the Verifier agent using Azure AI Foundry.

    Mechanical section-presence checks run locally (deterministic and fast). Phi-4-reasoning
    then performs a qualitative review of the generated files — going beyond section existence
    to ask whether the content is actually useful for the detected stack.
    """
    # Run mechanical verification locally
    result = run({
        "project_path": project_path,
        "execution_result": execution_result,
        "action_plan": action_plan,
    })

    # Build a summary of what was written and verified for Phi-4 to review
    verification_summary = json.dumps({
        "project_path": project_path,
        "verification_results": result.get("verification_results", []),
        "overall_pass": result.get("overall_pass", False),
        "recommendations": result.get("recommendations", []),
    }, indent=2)

    reasoning_prompt = (
        "You are a senior developer reviewing config files just written to a project.\n\n"
        f"Verification results:\n{verification_summary}\n\n"
        "In 3-5 sentences:\n"
        "1. Are the written files likely to actually improve the developer's AI tool experience, "
        "   or are they just template boilerplate?\n"
        "2. What's the single most important thing the developer should still do manually?\n"
        "3. Any risk of a written file causing problems?\n\n"
        "End with: QUALITY: excellent, good, acceptable, or needs_work"
    )

    try:
        model = os.environ.get("FOUNDRY_MODEL_DEPLOYMENT_NAME", "Phi-4-reasoning")
        chat = client.inference.get_chat_completions_client()
        response = chat.complete(
            model=model,
            messages=[{"role": "user", "content": reasoning_prompt}],
        )
        raw = response.choices[0].message.content
        result["_reasoning_trace"] = raw

        # Extract quality rating if present
        for line in reversed(raw.strip().splitlines()):
            stripped = line.strip().upper()
            if stripped.startswith("QUALITY:"):
                quality = stripped.split(":", 1)[1].strip().lower()
                if quality in ("excellent", "good", "acceptable", "needs_work"):
                    result["quality_rating"] = quality
                break
    except Exception:
        pass  # reasoning trace is optional

    return result

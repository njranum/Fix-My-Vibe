"""
src/agents/remediator.py
Remediator agent (Foundry/Tier B+C): KB-grounded code fixes for the semantic findings
the deterministic transforms can't safely handle — SQL injection, eval/exec, shell=True
(Tier B) and hardcoded secrets (Tier C, never claimed "fixed" — always a rotation
follow-up).

Design (per docs/code-remediation-plan.md §6, after review):
  - DIRECT KB call, not a free-roaming agent loop: Python calls `kb_search(...)`
    (deterministic, testable), then passes the chunks to ONE batched LLM run.
  - ONE run for ALL findings (not one agent per finding) — the repo's rate-limit
    history makes per-finding spawning unsafe.
  - Every returned patch is proven by the SAME harness (verify_patch) before it
    becomes an action. Citations are a soft signal, not a hard gate.

The module is split into pure helpers (query building, prompt assembly, patch parsing,
verify-and-build) and a thin `run_with_foundry` orchestrator, so the logic is unit-
testable with a mocked kb_search and a recorded LLM response — no Azure needed.
"""

import os
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.tools import remediation as rem
from src.foundry_utils import (
    get_last_assistant_message_with_reasoning,
    parse_json_response,
    create_thread_and_send,
    run_agent_with_tools,
)

TIER_B_TYPES = frozenset({"sql_injection", "code_injection", "shell_injection_risk"})
TIER_C_TYPES = frozenset({"hardcoded_secret"})
REMEDIABLE_TYPES = TIER_B_TYPES | TIER_C_TYPES

# finding type -> (KB query text, threat_filter)
_KB_QUERY = {
    "sql_injection": ("SQL injection fix parameterised query bound parameters", "injection"),
    "code_injection": ("eval exec code injection safe alternative ast.literal_eval", "injection"),
    "shell_injection_risk": ("shell=True command injection subprocess argument list shlex", "injection"),
    "hardcoded_secret": ("hardcoded secret api key move to environment variable rotate", "secrets"),
}

_ROTATION_NOTE = "Rotate this credential immediately — it must be considered compromised."

# Prefer a specific framework/language label for the KB stack hint.
_STACK_PRIORITY = ["fastapi", "django", "flask", "react", "nextjs", "vite",
                   "typescript", "node", "python"]


REMEDIATOR_INSTRUCTIONS = """
You are the Remediator agent for Fix My Vibe. You produce MINIMAL, correct code fixes
for security findings, grounded in the provided knowledge-base excerpts.

You receive a JSON object with:
- findings: each has file, line, finding_type, severity, and the exact source line(s)
- kb_context: authoritative guidance (OWASP/CWE/NIST + framework docs) per finding_type

Rules:
- Produce the SMALLEST change that fixes the finding. Prefer a single-line replacement.
  If the fix needs an import (e.g. `import ast`, `import shlex`), include it by making
  `original`/`proposed` span the necessary lines.
- `original` MUST be copied EXACTLY from the provided source line(s) — character for
  character — so it can be located in the file. Never paraphrase it.
- Base the fix on kb_context. Do not invent APIs.
- For hardcoded secrets: move the value to an environment variable
  (os.environ / process.env). NEVER keep the literal. The secret is already
  compromised — set requires_followup to a rotation instruction.
- Do NOT fix anything not in findings. One patch per finding.

Output ONLY this JSON (no fences, no preamble):
{
  "patches": [
    {
      "file": "app/main.py", "line": 24, "finding_type": "sql_injection",
      "original": "    cursor.execute(f\\"SELECT * FROM orders WHERE c = '{name}'\\")",
      "proposed": "    cursor.execute(\\"SELECT * FROM orders WHERE c = ?\\", (name,))",
      "rationale": "Parameterised query — values bound, never interpolated.",
      "requires_followup": null,
      "confidence": "high"
    }
  ]
}
"""


def primary_stack(stack: list[str]) -> str:
    """Pick the most specific stack label for the KB hint."""
    for s in _STACK_PRIORITY:
        if s in stack:
            return s
    return stack[0] if stack else "python"


def build_kb_query(finding_type: str, stack: str) -> tuple[str, str, str]:
    """Return (query, stack_filter, threat_filter) for a finding type. Pure."""
    query, threat = _KB_QUERY.get(finding_type, (finding_type.replace("_", " "), None))
    return query, stack, threat


def fetch_kb_context(finding_types: set[str], stack: str, kb_search_fn) -> dict:
    """Query the KB once per distinct finding type (cached). Returns
    {finding_type: [result, ...]}. kb_search_fn is injected for testability."""
    context: dict[str, list] = {}
    for ftype in sorted(finding_types):
        query, stack_filter, threat_filter = build_kb_query(ftype, stack)
        try:
            res = kb_search_fn(query, stack_filter, threat_filter)
        except Exception as e:  # KB unavailable shouldn't kill remediation
            res = {"results": [], "error": str(e)}
        context[ftype] = res.get("results", [])
    return context


def _remediable_findings(scan_result: dict) -> list[dict]:
    """Tier B/C findings in Python files (v1 is Python-only)."""
    out = []
    for f in scan_result.get("code_security_findings", []):
        if f.get("type") in REMEDIABLE_TYPES and str(f.get("file", "")).endswith(".py"):
            out.append(f)
    return out


def build_task_message(findings_with_src: list[dict], kb_context: dict) -> str:
    """Assemble the single batched prompt payload. Pure."""
    return json.dumps({"findings": findings_with_src, "kb_context": kb_context}, indent=2)


def parse_patches(raw: str) -> list[dict]:
    """Parse the model's JSON into a list of patch dicts. Tolerant of a bare list."""
    parsed = parse_json_response(raw)
    if isinstance(parsed, dict):
        return parsed.get("patches", [])
    if isinstance(parsed, list):
        return parsed
    return []


def verify_and_build_actions(
    scan_result: dict, patches: list[dict], kb_context: dict, start_rank: int
) -> tuple[list[dict], int]:
    """Turn raw model patches into VERIFIED remediate actions.

    Each patch is applied to an in-memory copy and proven (parse + finding cleared +
    no new findings) before becoming an action. Unverifiable patches are dropped.
    Tier-C (secret) actions always carry a rotation follow-up. Reads source files;
    never writes.
    """
    project_path = scan_result.get("project_path", "")
    actions: list[dict] = []
    rank = start_rank
    cache: dict[str, str | None] = {}

    for patch in patches:
        ftype = patch.get("finding_type")
        rel = patch.get("file", "")
        line = patch.get("line")
        original = patch.get("original")
        proposed = patch.get("proposed")
        if ftype not in REMEDIABLE_TYPES or not rel.endswith(".py"):
            continue
        if not line or original is None or proposed is None:
            continue

        if rel not in cache:
            try:
                cache[rel] = (Path(project_path) / rel).read_text(encoding="utf-8")
            except OSError:
                cache[rel] = None
        text = cache[rel]
        if text is None:
            continue

        add_imports = rem.detect_needed_imports(text, proposed)
        try:
            patched = rem.build_patched(text, line, original.rstrip("\n"),
                                        proposed.rstrip("\n"), add_imports)
        except ValueError:
            continue  # model's `original` didn't match the file — refuse
        verdict = rem.verify_patch(text, patched, ftype, is_python=True, file_label=rel)
        if not verdict.get("ok"):
            continue

        tier = "manual" if ftype in TIER_C_TYPES else "assisted"
        followup = _ROTATION_NOTE if ftype in TIER_C_TYPES else patch.get("requires_followup")
        citations = [
            {"title": c.get("title", ""), "url": c.get("url", "")}
            for c in kb_context.get(ftype, [])[:3]
        ]
        sev = next((f.get("severity", "medium")
                    for f in scan_result.get("code_security_findings", [])
                    if f.get("type") == ftype and f.get("file") == rel and f.get("line") == line),
                   "high")
        actions.append({
            "rank": rank,
            "tool": "security",
            "action": "remediate",
            "file": rel,
            "line": line,
            "finding_type": ftype,
            "severity": sev,
            "tier": tier,
            "expected_line": original.rstrip("\n"),
            "proposed_line": proposed.rstrip("\n"),
            "add_imports": add_imports,
            "patch": rem.make_unified_diff(text, patched, rel),
            "rationale": patch.get("rationale", ""),
            "kb_citations": citations,
            "requires_followup": followup,
            "confidence": patch.get("confidence", "medium"),
            "verification": verdict,
            "priority": "high" if sev == "high" else "medium",
            "content": None,
            "expected_sections": [],
            "estimated_tokens": 0,
            "reason": f"{ftype} at {rel}:{line} — KB-grounded fix (verified)",
        })
        rank += 1

    return actions, rank


def _attach_source(findings: list[dict], project_path: str) -> list[dict]:
    """Attach the exact source line to each finding for the prompt (the scanner's
    snippet is truncated/redacted, so re-read from the file)."""
    out = []
    cache: dict[str, list[str]] = {}
    for f in findings:
        rel = f.get("file", "")
        if rel not in cache:
            try:
                cache[rel] = (Path(project_path) / rel).read_text(encoding="utf-8").splitlines()
            except OSError:
                cache[rel] = []
        lines = cache[rel]
        line_no = f.get("line", 0)
        src = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
        out.append({
            "file": rel, "line": line_no, "finding_type": f.get("type"),
            "severity": f.get("severity", "medium"), "source_line": src,
        })
    return out


def run_with_foundry(client, scan_result: dict, start_rank: int = 1, kb_search_fn=None):
    """Generate verified Tier-B/C remediations via one batched LLM run grounded in the
    KB. Returns (actions, next_rank). Safe-by-default: any failure yields no actions
    rather than killing the plan.
    """
    if kb_search_fn is None:
        from src.agents.researcher import kb_search as kb_search_fn

    findings = _remediable_findings(scan_result)
    if not findings:
        return [], start_rank

    stack = primary_stack(scan_result.get("detected_stack", []))
    kb_context = fetch_kb_context({f["type"] for f in findings}, stack, kb_search_fn)
    findings_with_src = _attach_source(findings, scan_result.get("project_path", ""))

    agent = client.agents.create_agent(
        model=os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"],
        name="fix-my-vibe-remediator",
        instructions=REMEDIATOR_INSTRUCTIONS,
        tools=[],
    )
    try:
        task = (
            "Produce minimal, KB-grounded fixes for the findings below. "
            "Return ONLY the patches JSON.\n\n"
            + build_task_message(findings_with_src, kb_context)
        )
        thread_id = create_thread_and_send(client, task)
        run_agent_with_tools(client, agent.id, thread_id, {}, max_iterations=400)
        raw, _reasoning = get_last_assistant_message_with_reasoning(client, thread_id)
        patches = parse_patches(raw)
    finally:
        client.agents.delete_agent(agent.id)

    return verify_and_build_actions(scan_result, patches, kb_context, start_rank)

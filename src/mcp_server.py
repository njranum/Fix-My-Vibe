"""
src/mcp_server.py
Fix My Vibe — local MCP server (stdio transport).

Exposes the same Scanner → Researcher → Planner → Executor → Verifier pipeline the
CLI uses, as three MCP tools:

  - scan_project   read-only diagnosis (Scanner only)
  - propose_fixes  dry run — full ranked plan with file content, no writes
  - apply_fixes    single-call action tool; uses an MCP elicitation prompt (one
                   checkbox per proposed fix) as the confirmation gate, then writes
                   only the ticked fixes

Both the CLI and this server call the shared orchestrator phase functions
(run_plan_phase / run_apply_phase), so output is identical.

Safety: only run_apply_phase writes, only for elicitation-confirmed ranks. If the
client doesn't support elicitation, apply_fixes writes nothing and tells the caller
to review via propose_fixes (fail safe, not open).

Protocol note: stdio MCP uses stdout for the JSON-RPC channel. The underlying agents
print progress to stdout, so every phase call is wrapped in redirect_stdout(stderr).
"""

import os
import sys
import hashlib
import contextlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pydantic import BaseModel, create_model, Field
from mcp.server.fastmcp import FastMCP, Context
import mcp.types as mcp_types

from src.orchestrator import _resolve_mode, run_plan_phase, run_apply_phase

# Load .env if present (mirrors cli.py) so FOUNDRY_PROJECT_ENDPOINT etc. are picked up.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


mcp = FastMCP("fix-my-vibe")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _quiet():
    """Redirect agent stdout chatter to stderr so it never corrupts the JSON-RPC
    channel on stdout."""
    return contextlib.redirect_stdout(sys.stderr)


# In-process cache of the most recent plan per project. The diagnose+plan phase
# (Researcher + Planner + Remediator — the expensive LLM work) is identical between
# propose_fixes and apply_fixes, so apply reuses the plan propose just computed
# instead of re-running it. Guarded by a file signature: any change to the project
# invalidates the cache, so a stale plan is never applied.
_plan_cache: dict[str, dict] = {}

_SIG_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules",
                  ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build"}


def _project_signature(project_path: str) -> str:
    """A cheap fingerprint of the project's files (relative path + size + mtime).
    Changes whenever any file is added, removed, or edited — so a cached plan is
    reused only while the project is untouched (e.g. between propose and apply)."""
    root = Path(project_path)
    parts: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SIG_SKIP_DIRS]
        for name in filenames:
            if name.endswith(".bak"):
                continue
            fp = Path(dirpath) / name
            try:
                st = fp.stat()
            except OSError:
                continue
            parts.append(f"{fp.relative_to(root)}:{st.st_size}:{st.st_mtime_ns}")
    parts.sort()
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _cached_plan_phase(project_path: str, mode: str) -> dict:
    """run_plan_phase, memoised per (resolved path, mode) and guarded by the project
    signature. Reuses propose_fixes' plan in apply_fixes when nothing changed in
    between, saving a full re-run of the LLM plan phase. Errors are never cached."""
    key = str(Path(project_path).resolve())
    sig = _project_signature(project_path)
    hit = _plan_cache.get(key)
    if hit and hit["mode_arg"] == mode and hit["signature"] == sig:
        return hit["plan_phase"]
    plan_phase = run_plan_phase(project_path, mode=mode)
    if "error" not in plan_phase:
        _plan_cache[key] = {"signature": sig, "mode_arg": mode, "plan_phase": plan_phase}
    return plan_phase


def _strip_reasoning(obj):
    """Recursively drop _reasoning_trace keys (Foundry traces — large and noisy)
    before returning a payload to the MCP client."""
    if isinstance(obj, dict):
        return {k: _strip_reasoning(v) for k, v in obj.items() if k != "_reasoning_trace"}
    if isinstance(obj, list):
        return [_strip_reasoning(v) for v in obj]
    return obj


def _is_writable(action: dict) -> bool:
    """An action changes the project if it's a code remediation (targeted in-place
    edit, no `content`) or a file write (has `content` and isn't an 'improve' note).
    Mirrors executor.run's apply logic."""
    if action.get("action") == "remediate":
        return True
    return action.get("action") != "improve" and action.get("content") is not None


def _action_label(action: dict) -> str:
    """Human-readable checkbox label, e.g.
    'Apply CLAUDE.md (high) — Claude Code detected but no CLAUDE.md'."""
    file = action.get("file", "?")
    priority = action.get("priority", "medium")
    reason = action.get("reason", "")
    label = f"Apply {file} ({priority})"
    if reason:
        label += f" — {reason}"
    return label


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #

@mcp.tool()
def scan_project(project_path: str, mode: str = "auto") -> dict:
    """Read-only diagnosis of a project's AI coding tool setup.

    Runs the Scanner agent only — detects AI tools, tech stack, and security
    issues. Never writes files.

    Args:
        project_path: Absolute path to the project directory to scan.
        mode: "auto" (default), "local", or "foundry".
    """
    path = Path(project_path).resolve()
    if not path.exists():
        return {"error": f"Path does not exist: {project_path}"}

    resolved = _resolve_mode(mode)
    with _quiet():
        if resolved == "foundry":
            from src.foundry_utils import get_client
            from src.agents import scanner
            try:
                client = get_client()
                result = scanner.run_with_foundry(client, str(path))
            except Exception as e:
                print(f"  Foundry unavailable ({e}) — falling back to local mode")
                resolved = "local"
                from src.agents import scanner as scanner_local
                result = scanner_local.run({"project_path": str(path)})
        else:
            from src.agents import scanner
            result = scanner.run({"project_path": str(path)})

    result = _strip_reasoning(result)
    return {
        "mode": resolved,
        "detected_tools": result.get("detected_tools", []),
        "detected_stack": result.get("detected_stack", []),
        "security_issues": result.get("security_issues", []),
        "code_security_findings": result.get("code_security_findings", []),
        "missing_configs": result.get("missing_configs", {}),
        "diagnosis_summary": result.get("diagnosis_summary", ""),
        "priority": result.get("priority", "low"),
    }


@mcp.tool()
def propose_fixes(project_path: str, mode: str = "auto") -> dict:
    """Dry run: produce the full ranked plan of fixes, including complete file
    content, WITHOUT writing anything.

    Use this to preview what apply_fixes would do, or from clients that don't
    support elicitation.

    Args:
        project_path: Absolute path to the project directory.
        mode: "auto" (default), "local", or "foundry".
    """
    with _quiet():
        plan_phase = _cached_plan_phase(project_path, mode)
    if "error" in plan_phase:
        return plan_phase

    plan_result = _strip_reasoning(plan_phase["plan_result"])
    actions = plan_result.get("actions", [])
    return {
        "mode": plan_phase["mode"],
        "actions": actions,
        "plan_summary": plan_result.get("plan_summary", ""),
        "convention_summary": plan_result.get("convention_summary", ""),
    }


@mcp.tool()
async def apply_fixes(project_path: str, ctx: Context, mode: str = "auto") -> dict:
    """Diagnose the project, ask which proposed fixes to apply, then write the
    selected files (with automatic .bak backups) and verify them.

    Confirmation is REQUIRED: an elicitation prompt presents one checkbox per
    proposed fix. Only ticked fixes are written. If the client doesn't support
    elicitation, nothing is written — review via propose_fixes instead.

    Args:
        project_path: Absolute path to the project directory.
        mode: "auto" (default), "local", or "foundry".
    """
    with _quiet():
        plan_phase = _cached_plan_phase(project_path, mode)
    if "error" in plan_phase:
        return plan_phase

    resolved_mode = plan_phase["mode"]
    plan_result = plan_phase["plan_result"]
    actions = plan_result.get("actions", [])

    if not actions:
        return {"status": "nothing_to_do",
                "message": "Nothing to fix — your AI tool setup looks good."}

    writable = [a for a in actions if _is_writable(a)]
    notes = [a for a in actions if not _is_writable(a)]

    if not writable:
        return {
            "status": "nothing_to_do",
            "message": "Only manual-edit ('improve') suggestions found — nothing to write.",
            "notes": [_action_label(a) for a in notes],
        }

    # Fail safe: never write without an interactive confirmation channel.
    if not ctx.session.check_client_capability(
        mcp_types.ClientCapabilities(elicitation=mcp_types.ElicitationCapability())
    ):
        return {
            "status": "needs_review",
            "message": (
                "This client does not support elicitation, so apply_fixes cannot "
                "obtain confirmation and will not write any files. Review the plan "
                "with propose_fixes and apply changes manually."
            ),
            "plan": _strip_reasoning(plan_result),
        }

    # Build a dynamic elicitation schema: one bool field per writable action,
    # keyed by rank. High-priority fixes default to checked.
    fields: dict = {}
    field_to_rank: dict[str, int] = {}
    for a in writable:
        rank = a.get("rank")
        field_name = f"fix_{rank}"
        field_to_rank[field_name] = rank
        # Code edits (remediate) always default UNCHECKED — editing source is a
        # higher-trust action than writing a new config file. Config fixes keep the
        # high-priority default.
        default_checked = a.get("priority") == "high" and a.get("action") != "remediate"
        fields[field_name] = (
            bool,
            Field(default=default_checked, title=_action_label(a)),
        )

    DynModel: type[BaseModel] = create_model("FixSelection", **fields)

    note_lines = ""
    if notes:
        note_lines = "\n\nManual-edit suggestions (not written automatically):\n" + \
            "\n".join(f"  • {_action_label(a)}" for a in notes)
    message = (
        f"Fix My Vibe found {len(writable)} proposed fix(es) for "
        f"{Path(project_path).resolve()}. Select which to apply." + note_lines
    )

    result = await ctx.elicit(message=message, schema=DynModel)

    if result.action != "accept" or result.data is None:
        return {"status": "cancelled", "message": "No files written — confirmation declined."}

    confirmed_ranks = [
        rank for field_name, rank in field_to_rank.items()
        if getattr(result.data, field_name, False)
    ]
    if not confirmed_ranks:
        return {"status": "cancelled", "message": "No files written — nothing selected."}

    with _quiet():
        apply_phase = run_apply_phase(
            project_path, plan_result, confirmed_ranks, mode=resolved_mode
        )

    return {
        "status": "completed",
        "mode": resolved_mode,
        "confirmed_ranks": confirmed_ranks,
        "execution_result": _strip_reasoning(apply_phase["execution_result"]),
        "verify_result": _strip_reasoning(apply_phase["verify_result"]),
    }


def main() -> None:
    """Console entry point — run the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()

"""
src/cli.py
Fix My Vibe — CLI entrypoint.
Usage: fix-my-vibe <project_path> [--local] [--verbose] [--json]
"""

import sys
import os
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fix-my-vibe",
        description="Diagnose and fix your AI coding tool setup",
    )
    parser.add_argument(
        "project_path",
        help="Path to the project directory to scan",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Run in local mode (no Azure Foundry — uses built-in reasoning)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full JSON output from each agent",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output final result as JSON (useful for CI/scripting)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Auto-confirm all actions (non-interactive mode)",
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Only run the Scanner agent — no planning or file writes",
    )
    parser.add_argument(
        "--undo",
        action="store_true",
        help="Restore files from Fix My Vibe backups (undo a previous run's edits)",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Emit per-phase timing instrumentation to .fmv-traces/ and print a "
             "wall-time breakdown (diagnose where the pipeline spends its time)",
    )

    args = parser.parse_args()

    project_path = Path(args.project_path).resolve()
    if not project_path.exists():
        print(f"Error: path does not exist: {project_path}", file=sys.stderr)
        sys.exit(1)
    if not project_path.is_dir():
        print(f"Error: not a directory: {project_path}", file=sys.stderr)
        sys.exit(1)

    # Load .env if present
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # Resolve the effective mode ONCE (after .env load) so the trace label and
    # the run branch agree. Foundry needs both --local absent AND an endpoint set.
    foundry = not args.local and bool(os.environ.get("FOUNDRY_PROJECT_ENDPOINT"))
    effective_mode = "foundry" if foundry else "local"

    # Start timing instrumentation before any agent runs (no-op unless --trace
    # or FMV_TRACE=1). Label the trace file with the RESOLVED mode, not the flag.
    from src.tracing import init_tracing, print_summary
    init_tracing(args.trace, run_label=effective_mode)
    if args.trace and not foundry:
        why = "--local passed" if args.local else "FOUNDRY_PROJECT_ENDPOINT not set"
        print(f"  Note: tracing a LOCAL run ({why}) — cloud metrics "
              f"(model round-trips, polls, provisioning) will be zero.\n", file=sys.stderr)

    if args.undo:
        _run_undo(str(project_path), assume_yes=args.yes)
        return

    confirm_fn = None
    if args.yes:
        confirm_fn = lambda plan: [a["rank"] for a in plan.get("actions", [])]

    if args.scan_only:
        _run_scan_only(str(project_path), args.verbose, args.output_json, use_local=args.local)
        print_summary()
        return

    if not foundry:
        if not args.local:
            print("  Note: FOUNDRY_PROJECT_ENDPOINT not set — running in local mode")
        from src.orchestrator import run_local
        result = run_local(str(project_path), confirm_fn=confirm_fn, verbose=args.verbose)
    else:
        from src.orchestrator import run_with_foundry
        result = run_with_foundry(str(project_path), confirm_fn=confirm_fn, verbose=args.verbose)

    if args.output_json:
        print(json.dumps(result, indent=2))
    else:
        _print_final_summary(result)
        _maybe_print_undo_hint(result, str(project_path))

    print_summary()


def _run_scan_only(project_path: str, verbose: bool, output_json: bool, use_local: bool = False) -> None:
    print(f"\n  Fix My Vibe — scan only: {project_path}\n")

    if not use_local and os.environ.get("FOUNDRY_PROJECT_ENDPOINT"):
        from src.foundry_utils import get_client
        from src.agents.scanner import run_with_foundry
        from src import tracing
        print("  Using Azure Foundry (pass --local to force local mode)\n")
        client = get_client()
        with tracing.timed("scanner", kind="phase", set_phase=True):
            result = run_with_foundry(client, project_path)
    else:
        from src.agents.scanner import run as scanner_run
        result = scanner_run({"project_path": project_path})

    if output_json:
        print(json.dumps(result, indent=2))
        return

    print("── Scan Result ─────────────────────────────────────────")
    print(f"  Tools:    {', '.join(result.get('detected_tools', [])) or 'none detected'}")
    print(f"  Stack:    {', '.join(result.get('detected_stack', [])) or 'unknown'}")
    print(f"  Security: {len(result.get('security_issues', []))} issue(s)")
    print(f"  Missing:  {', '.join(result.get('missing_configs', {}).keys()) or 'none'}")
    print(f"\n  {result.get('diagnosis_summary', '')}")
    print("────────────────────────────────────────────────────────")

    if verbose:
        reasoning = result.pop("_reasoning_trace", None)
        if reasoning:
            print("\n── Phi-4 Reasoning Trace ──────────────────────────────────")
            for line in reasoning.splitlines():
                print(f"  {line}")
            print("────────────────────────────────────────────────────────────")
        print("\nFull scan result (JSON):")
        print(json.dumps(result, indent=2))


def _run_undo(project_path: str, assume_yes: bool = False) -> None:
    """Restore files from Fix My Vibe backups — undo a previous run's edits."""
    from src.tools.remediation import find_backups, restore_backups

    base = Path(project_path)
    backups = find_backups(project_path)
    if not backups:
        print("\n  No Fix My Vibe backups found — nothing to undo.\n")
        return

    print(f"\n  Fix My Vibe — undo: restore {len(backups)} file(s) from backup:")
    for target in sorted(backups):
        try:
            shown = Path(target).relative_to(base.resolve())
        except ValueError:
            shown = Path(target).name
        print(f"    • {shown}")
    print("  (newly-created files are left in place — undo only reverts overwritten/edited files)")

    if not assume_yes:
        try:
            choice = input("\n  Restore these files and remove backups? [y/N]: ").strip().lower()
        except EOFError:
            choice = "n"
        if choice != "y":
            print("  Cancelled. Nothing changed.\n")
            return

    restored = restore_backups(project_path)
    print(f"\n  Restored {len(restored)} file(s) from backup.\n")


def _maybe_print_undo_hint(result: dict, project_path: str) -> None:
    """After a run that edited source files, tell the user how to undo it."""
    executed = result.get("execution_result", {}).get("executed", [])
    if any(e.get("status") == "remediated" for e in executed):
        print(f"  Undo the code fixes with:  fix-my-vibe {project_path} --undo\n")


def _print_final_summary(result: dict) -> None:
    status = result.get("status", "unknown")
    print("\n" + "=" * 60)

    if status == "completed":
        exec_result = result.get("execution_result", {})
        verify_result = result.get("verify_result", {})
        written = len(exec_result.get("executed", []))
        print(f"  Fix My Vibe — Complete")
        print(f"  {written} file(s) written")
        print(f"  Verification: {verify_result.get('summary', '')}")
        recs = verify_result.get("recommendations", [])
        if recs:
            print("\n  Recommendations:")
            for r in recs:
                print(f"    • {r}")
    elif status == "cancelled":
        print("  Fix My Vibe — Cancelled (no files written)")
    elif status == "nothing_to_do":
        print("  Fix My Vibe — Nothing to fix!")
        print("  Your AI tool setup looks well-configured.")
    else:
        print(f"  Fix My Vibe — {status}")

    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()

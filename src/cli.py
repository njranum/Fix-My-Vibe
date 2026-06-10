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

    confirm_fn = None
    if args.yes:
        confirm_fn = lambda plan: [a["rank"] for a in plan.get("actions", [])]

    if args.scan_only:
        _run_scan_only(str(project_path), args.verbose, args.output_json, use_local=args.local)
        return

    if args.local or not os.environ.get("FOUNDRY_PROJECT_ENDPOINT"):
        if not args.local and not os.environ.get("FOUNDRY_PROJECT_ENDPOINT"):
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


def _run_scan_only(project_path: str, verbose: bool, output_json: bool, use_local: bool = False) -> None:
    print(f"\n  Fix My Vibe — scan only: {project_path}\n")

    if not use_local and os.environ.get("FOUNDRY_PROJECT_ENDPOINT"):
        from src.foundry_utils import get_client
        from src.agents.scanner import run_with_foundry
        print("  Using Azure Foundry (pass --local to force local mode)\n")
        client = get_client()
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

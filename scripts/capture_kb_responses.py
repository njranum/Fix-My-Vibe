#!/usr/bin/env python3
"""
scripts/capture_kb_responses.py
Capture real Azure AI Search responses for each remediable finding type and save
them as test fixtures. Run once (with live Azure credentials) to refresh the
contract-test fixtures in tests/fixtures/kb_responses/.

    AZURE_SEARCH_ENDPOINT=... AZURE_SEARCH_KEY=... \\
        python scripts/capture_kb_responses.py

The contract tests (tests/test_remediator.py) mock kb_search to return these
captured chunks, so the remediator's query-building and citation logic is tested
against REAL knowledge-base data without CI needing Azure.
"""

import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.remediator import REMEDIABLE_TYPES, build_kb_query, primary_stack
from src.agents.researcher import kb_search

OUT_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "kb_responses"


def main() -> int:
    if not (os.environ.get("AZURE_SEARCH_ENDPOINT") and os.environ.get("AZURE_SEARCH_KEY")):
        print("AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_KEY must be set.", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Capture against a representative stack; adjust if your demo target differs.
    stack = primary_stack(["python", "fastapi"])

    for ftype in sorted(REMEDIABLE_TYPES):
        query, stack_filter, threat_filter = build_kb_query(ftype, stack)
        result = kb_search(query, stack_filter, threat_filter)
        results = result.get("results", [])
        out = OUT_DIR / f"{ftype}.json"
        out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"  {ftype}: {len(results)} chunk(s) -> {out.relative_to(OUT_DIR.parent.parent)}")
        if result.get("error"):
            print(f"    WARNING: {result['error']}", file=sys.stderr)

    print("Done. Commit the refreshed fixtures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

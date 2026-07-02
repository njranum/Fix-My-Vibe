#!/usr/bin/env bash
#
# Repeatable Fix My Vibe demo runner.
#
# Each run makes a FRESH throwaway copy of the demo project, then runs the
# pipeline against that copy. The master fixture is never touched, so you can
# run this as many times as you like and always start from the same 5 problems.
#
# Usage:
#   bash demo/run.sh                 # interactive — shows the confirmation gate (BEST for live demo)
#   bash demo/run.sh --verbose       # also prints each agent's reasoning trace
#   bash demo/run.sh --yes           # hands-off — auto-applies everything (good for a warm-up)
#   bash demo/run.sh --yes --trace   # warm-up + per-phase timing breakdown
#
# Override the interpreter with PYTHON=... if your venv lives elsewhere.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MASTER="tests/fixtures/demo-shop"     # source project (5 problems, exercises Foundry IQ)
WORK=".fmv-run/shop-api"              # throwaway working copy (gitignored)
PY="${PYTHON:-./.venv/bin/python}"

if [ ! -f "$MASTER/app.py" ]; then
  echo "✗ Master fixture missing at $MASTER — are you in the repo root?" >&2
  exit 1
fi
if [ ! -x "$PY" ] && ! command -v "$PY" >/dev/null 2>&1; then
  echo "✗ Python not found at '$PY'. Activate the venv or set PYTHON=..." >&2
  exit 1
fi

# Fresh copy every run — this is what makes the demo repeatable.
rm -rf "$WORK"
mkdir -p "$(dirname "$WORK")"
cp -R "$MASTER" "$WORK"

echo
echo "────────────────────────────────────────────────────────────"
echo "  Fix My Vibe — run on a fresh copy: $WORK"
echo "  (source $MASTER is untouched; this copy is reset every run)"
echo "────────────────────────────────────────────────────────────"
echo

"$PY" -m src.cli "$WORK" "$@"

echo
echo "Generated files are in $WORK — inspect them, then re-run to reset."

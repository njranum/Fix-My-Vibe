#!/usr/bin/env bash
#
# Pre-demo sanity check. Run this BEFORE you present — it catches the things
# that silently send the pipeline into local mode (no Foundry IQ) or make it
# crash, which were the exact failure modes we hit while building this.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
PY="${PYTHON:-./.venv/bin/python}"

pass=0; fail=0
check() {  # name, command
  if eval "$2" >/dev/null 2>&1; then echo "  ✓ $1"; pass=$((pass+1));
  else echo "  ✗ $1"; fail=$((fail+1)); fi
}

echo "Fix My Vibe — demo preflight"
echo

check "venv python has deps (dotenv + azure SDK)" \
  "$PY -c 'import dotenv, azure.ai.projects'"
check "FOUNDRY_PROJECT_ENDPOINT set (else it runs LOCAL — no Foundry IQ)" \
  "$PY -c 'from dotenv import load_dotenv; load_dotenv(); import os,sys; sys.exit(0 if os.environ.get(\"FOUNDRY_PROJECT_ENDPOINT\") else 1)'"
check "AZURE_SEARCH_ENDPOINT set (the KB behind Foundry IQ)" \
  "$PY -c 'from dotenv import load_dotenv; load_dotenv(); import os,sys; sys.exit(0 if os.environ.get(\"AZURE_SEARCH_ENDPOINT\") else 1)'"
check "demo master fixture present" \
  "test -f tests/fixtures/demo-shop/app.py"

echo
if [ "$fail" -eq 0 ]; then
  echo "READY ✓  — do one warm-up run before going live (real cloud, ~1.5 min):"
  echo "    bash demo/run.sh --yes"
else
  echo "$fail check(s) failed — fix before demoing."
  echo "Tip: most failures are the wrong interpreter. Use the venv:"
  echo "    source .venv/bin/activate    (or set PYTHON=./.venv/bin/python)"
fi
echo
echo "Note: Azure auth (DefaultAzureCredential) isn't checked here — the"
echo "warm-up run is the real test that credentials work."

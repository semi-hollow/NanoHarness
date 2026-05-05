#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v python3.11 >/dev/null 2>&1; then
  echo "python3.11 is required but was not found on PATH." >&2
  exit 1
fi

echo "== Agent Forge verification =="
echo "Working directory: $(pwd)"
echo

echo "== Compile Python files =="
python3.11 - <<'PY'
from pathlib import Path
import py_compile

for path in Path(".").rglob("*.py"):
    py_compile.compile(str(path), doraise=True)

print("py_compile passed")
PY
echo

echo "== Single-agent demo =="
python3.11 run_demo.py --mode single
echo

echo "== Multi-agent demo =="
python3.11 run_demo.py --mode multi
echo

echo "== Workflow demo =="
python3.11 run_demo.py --mode workflow
echo

echo "== Unit tests =="
python3.11 -m unittest discover tests
echo

echo "== Eval benchmark =="
python3.11 -m agent_forge.eval.eval_runner
echo

echo "Verification passed."

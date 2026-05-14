#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")/.."

if [ -z "${PYTHON_BIN:-}" ]; then
  if [ -x ".venv/bin/python3.11" ]; then
    PYTHON_BIN=".venv/bin/python3.11"
  elif [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
  else
    PYTHON_BIN="python3.11"
  fi
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  if [ -x ".venv/bin/python3.11" ]; then
    PYTHON_BIN=".venv/bin/python3.11"
  elif [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
  else
    echo "Could not find ${PYTHON_BIN} or a local .venv Python." >&2
    exit 1
  fi
fi

echo "== Agent Forge verification =="
echo "Working directory: $(pwd)"
echo "Using Python: $(${PYTHON_BIN} --version 2>&1) at $(command -v "${PYTHON_BIN}" 2>/dev/null || printf '%s' "${PYTHON_BIN}")"
echo

echo "== Compile Python files =="
"${PYTHON_BIN}" -m compileall agent_forge tests eval_cases examples
echo

echo "== Single-agent demo =="
"${PYTHON_BIN}" run_demo.py --mode single --trace-file trace-verify-single.json
echo

echo "== Multi-agent demo =="
"${PYTHON_BIN}" run_demo.py --mode multi --trace-file trace-verify-multi.json
echo

echo "== Workflow demo =="
"${PYTHON_BIN}" run_demo.py --mode workflow
echo

echo "== Unit tests =="
"${PYTHON_BIN}" -m unittest discover tests
echo

echo "== Eval benchmark =="
"${PYTHON_BIN}" -m agent_forge.eval.eval_runner
echo

echo "Verification passed."

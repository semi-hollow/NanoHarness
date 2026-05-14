#!/usr/bin/env bash
set -Eeuo pipefail

if [ ! -f "run_demo.py" ] || [ ! -d "agent_forge" ]; then
  echo "scripts/verify.sh must be run from the agent-forge directory." >&2
  exit 1
fi

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

echo "Using Python: $(${PYTHON_BIN} --version 2>&1) at $(command -v "${PYTHON_BIN}" 2>/dev/null || printf '%s' "${PYTHON_BIN}")"

"${PYTHON_BIN}" run_demo.py --mode single --trace-file trace-verify-single.json
"${PYTHON_BIN}" run_demo.py --mode multi --trace-file trace-verify-multi.json
"${PYTHON_BIN}" run_demo.py --mode workflow
"${PYTHON_BIN}" -m unittest discover tests
"${PYTHON_BIN}" -m agent_forge.eval.eval_runner
"${PYTHON_BIN}" -m compileall agent_forge tests eval_cases examples

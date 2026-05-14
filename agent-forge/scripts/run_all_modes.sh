#!/usr/bin/env bash
set -Eeuo pipefail

if [ ! -f "run_demo.py" ] || [ ! -d "agent_forge" ]; then
  echo "scripts/run_all_modes.sh must be run from the agent-forge directory." >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  if [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
  else
    echo "Could not find ${PYTHON_BIN} or .venv/bin/python." >&2
    exit 1
  fi
fi

echo "Using Python: $(${PYTHON_BIN} --version 2>&1)"

"${PYTHON_BIN}" run_demo.py --mode single --trace-file trace-single.json
"${PYTHON_BIN}" -m json.tool trace-single.json > trace-single.pretty.json

"${PYTHON_BIN}" run_demo.py --mode multi --trace-file trace-multi.json
"${PYTHON_BIN}" -m json.tool trace-multi.json > trace-multi.pretty.json

"${PYTHON_BIN}" run_demo.py --mode workflow

echo "Generated trace-single.json, trace-single.pretty.json, trace-multi.json, trace-multi.pretty.json"

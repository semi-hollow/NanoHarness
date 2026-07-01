#!/usr/bin/env bash
set -Eeuo pipefail

# Purpose:
#   Local health check for the real CodingAgent runtime. It does not use simulated
#   model paths or teaching fixtures. If DEEPSEEK_API_KEY is available, it also
#   performs a small read-only real-model run against this repository.

cd "$(dirname "$0")/.."

if [ -z "${PYTHON_BIN:-}" ]; then
  # Prefer the venv's python3.11 compatibility link when present because older
  # setup flows and IDE configs may still look for that executable name.
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

VERIFY_DIR="${VERIFY_DIR:-.agent_forge/verify}"
mkdir -p "${VERIFY_DIR}"

# Compile catches syntax/import packaging problems before any agent run starts.
echo "== Compile Python files =="
"${PYTHON_BIN}" -m compileall -q agent_forge tests
echo

# This is only a smoke check. It does not prove benchmark quality.
echo "== Public CLI doctor =="
"${PYTHON_BIN}" -m agent_forge doctor
echo

echo "== Public CLI skills =="
"${PYTHON_BIN}" -m agent_forge skills list >/dev/null
echo

echo "== Unit smoke =="
"${PYTHON_BIN}" -m unittest discover tests
echo

if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  echo "== Real-model read-only smoke =="
  "${PYTHON_BIN}" -m agent_forge run "阅读这个项目结构并说明入口，不要修改文件" \
    --provider deepseek \
    --approval-mode locked \
    --max-steps "${VERIFY_REAL_MAX_STEPS:-4}" \
    --workspace . \
    --output-root "${VERIFY_DIR}/runs"
  echo
else
  echo "== Real-model read-only smoke skipped =="
  echo "DEEPSEEK_API_KEY is not set; configure it to verify the full agent run path."
  echo
fi

echo "Verification passed."
echo "Artifacts are under ${VERIFY_DIR}."

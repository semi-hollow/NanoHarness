#!/usr/bin/env bash
set -Eeuo pipefail

# Purpose:
#   Deterministic local health check for the repo.
#
# Why it stays on MockLLM:
#   verify.sh should be safe to run on macOS, WSL, and company machines without
#   internet access or API keys. Real effect validation belongs to
#   `forge bench swebench`, not this smoke script.

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

cleanup_fixtures() {
  # Some demos intentionally start by reintroducing a small bug so the agent has
  # something to fix. Keep verification from leaving that teaching fixture dirty.
  "${PYTHON_BIN}" - <<'PY'
from pathlib import Path

Path("examples/demo_repo/src/calculator.py").write_text(
    "def add(a: int, b: int) -> int:\n    return a + b\n",
    encoding="utf-8",
)
PY
}

reset_buggy_smoke_fixture() {
  "${PYTHON_BIN}" - <<'PY'
from pathlib import Path

Path("examples/demo_repo/src/calculator.py").write_text(
    "def add(a: int, b: int) -> int:\n    return a - b\n",
    encoding="utf-8",
)
PY
}

trap 'status=$?; cleanup_fixtures >/dev/null 2>&1 || true; exit "${status}"' EXIT

echo "== Agent Forge verification =="
echo "Working directory: $(pwd)"
echo "Using Python: $(${PYTHON_BIN} --version 2>&1) at $(command -v "${PYTHON_BIN}" 2>/dev/null || printf '%s' "${PYTHON_BIN}")"
echo

# Verification must be deterministic and free. Even if the developer's shell
# defaults to DeepSeek for personal runs, verify/eval should stay on MockLLM.
export AGENT_FORGE_DEFAULT_LLM="mock"

VERIFY_DIR="${VERIFY_DIR:-.agent_forge/verify}"
mkdir -p "${VERIFY_DIR}"

# Compile catches syntax/import packaging problems before any agent run starts.
echo "== Compile Python files =="
"${PYTHON_BIN}" -m compileall -q agent_forge examples
echo

# This is only a smoke check. It does not prove benchmark quality.
echo "== Public CLI doctor =="
"${PYTHON_BIN}" -m agent_forge doctor
echo

echo "== Mock runtime smoke =="
reset_buggy_smoke_fixture
"${PYTHON_BIN}" -m agent_forge run "修复 examples/demo_repo 里的测试失败问题" \
  --provider mock \
  --workspace . \
  --output-root "${VERIFY_DIR}/runs"
echo

echo "Verification passed."
echo "Smoke artifacts are under ${VERIFY_DIR}."

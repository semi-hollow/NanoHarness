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

echo "== Static type contracts =="
if ! "${PYTHON_BIN}" -m mypy --version >/dev/null 2>&1; then
  echo "mypy is missing; install the development tools with: python -m pip install -e '.[dev]'" >&2
  exit 1
fi
"${PYTHON_BIN}" -m mypy agent_forge
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
  SINGLE_OUTPUT="$("${PYTHON_BIN}" -m agent_forge run \
    "只调用 read_file 一次读取 pyproject.toml，然后立即说明包入口与 Python 版本要求，不要修改文件" \
    --provider deepseek \
    --approval-mode locked \
    --max-steps "${VERIFY_REAL_MAX_STEPS:-4}" \
    --workspace . \
    --execution-mode worktree \
    --no-keep-worktree \
    --output-root "${VERIFY_DIR}/runs")"
  printf '%s\n' "${SINGLE_OUTPUT}"
  SINGLE_RUN_DIR="$(printf '%s\n' "${SINGLE_OUTPUT}" | sed -n 's/^Run directory: //p' | tail -n 1)"
  if [ -z "${SINGLE_RUN_DIR}" ]; then
    echo "Could not identify the single-agent run directory from this command." >&2
    exit 1
  fi
  "${PYTHON_BIN}" - "${SINGLE_RUN_DIR}" <<'PY'
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
trace = json.loads((run / "trace.json").read_text(encoding="utf-8"))
usage = json.loads((run / "usage.json").read_text(encoding="utf-8"))
if trace.get("stop_reason") != "final_answer":
    raise SystemExit(f"real-model smoke did not complete: {trace.get('stop_reason')}")
if int((usage.get("summary") or {}).get("llm_calls") or 0) < 1:
    raise SystemExit("real-model smoke recorded no LLM call")
if (run / "patch.diff").read_text(encoding="utf-8").strip():
    raise SystemExit("read-only real-model smoke produced a candidate patch")
print(f"Validated single-agent evidence: {run}")
PY
  echo

  echo "== Real-model two-worker fanout smoke =="
  FANOUT_OUTPUT="$("${PYTHON_BIN}" -m agent_forge run \
    "并行审查 runtime 与 safety 证据，不要修改文件" \
    --agent-mode fanout \
    --fanout-plan examples/fanout-plan.sample.json \
    --max-workers 2 \
    --provider deepseek \
    --approval-mode locked \
    --max-steps "${VERIFY_REAL_FANOUT_MAX_STEPS:-8}" \
    --workspace . \
    --execution-mode worktree \
    --no-keep-worktree \
    --output-root "${VERIFY_DIR}/runs")"
  printf '%s\n' "${FANOUT_OUTPUT}"
  FANOUT_RUN_DIR="$(printf '%s\n' "${FANOUT_OUTPUT}" | sed -n 's/^Run directory: //p' | tail -n 1)"
  if [ -z "${FANOUT_RUN_DIR}" ]; then
    echo "Could not identify the fanout run directory from this command." >&2
    exit 1
  fi
  "${PYTHON_BIN}" - "${FANOUT_RUN_DIR}" <<'PY'
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
summary = json.loads((run / "fanout" / "fanout_summary.json").read_text(encoding="utf-8"))
results = summary.get("results") or []
if summary.get("status") != "passed" or summary.get("final_decision") != "PASS":
    raise SystemExit(
        f"real-model fanout did not pass: status={summary.get('status')} "
        f"final_decision={summary.get('final_decision')}"
    )
if not results or any(result.get("status") != "completed" for result in results):
    raise SystemExit(f"real-model fanout has incomplete workers: {results}")
if any(result.get("touched_files") for result in results):
    raise SystemExit("read-only real-model fanout modified a worker workspace")
metrics = summary.get("metrics") or {}
if int(metrics.get("llm_calls") or 0) < len(results) + 1:
    raise SystemExit("real-model fanout is missing worker or finalizer LLM usage")
if int(metrics.get("finalizer_llm_calls") or 0) < 1:
    raise SystemExit("real-model fanout finalizer did not run")
if (run / "patch.diff").read_text(encoding="utf-8").strip():
    raise SystemExit("read-only real-model fanout produced a candidate patch")
print(f"Validated live fanout evidence: {run}")
PY
  echo
else
  echo "== Real-model read-only smoke skipped =="
  echo "DEEPSEEK_API_KEY is not set; configure it to verify the full agent run path."
  echo
fi

echo "Verification passed."
echo "Artifacts are under ${VERIFY_DIR}."

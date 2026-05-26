#!/usr/bin/env bash
set -Eeuo pipefail

# Purpose:
#   Run the tiny single-agent demo with the real DeepSeek API.
#
# When to use:
#   Use this when you only want the shortest real-model smoke run. For the main
#   end-to-end engineering scenario, prefer run_webhook_deepseek.sh.
#
# Output:
#   .agent_forge/latest/single-deepseek/trace.json
#       Raw event stream for the tiny single-agent demo.
#   .agent_forge/latest/single-deepseek/usage_report.md
#       Main file to read for token/cost/context/tool summary.
#
# By default the calculator fixture is restored after the run so Git stays
# clean. To keep the model's patch for inspection:
#   KEEP_PATCH=1 local_scripts/run_deepseek.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

if [ ! -f ".venv/bin/activate" ]; then
  echo "Missing .venv. Run scripts/setup_macos_local.sh first." >&2
  exit 1
fi

if [ -z "${DEEPSEEK_API_KEY:-${AGENT_FORGE_API_KEY:-}}" ]; then
  echo "Missing DeepSeek API key. Export DEEPSEEK_API_KEY before running this script." >&2
  exit 1
fi

# DeepSeek's Chat Completions endpoint is OpenAI-compatible. These environment
# variables are the project-wide model gateway contract used by run_demo.py.
# You can override any of them before running the script:
#   DEEPSEEK_MODEL=deepseek-chat local_scripts/run_deepseek.sh
export AGENT_FORGE_BASE_URL="${AGENT_FORGE_BASE_URL:-${DEEPSEEK_BASE_URL:-https://api.deepseek.com}}"
export AGENT_FORGE_API_KEY="${AGENT_FORGE_API_KEY:-${DEEPSEEK_API_KEY:-}}"
export AGENT_FORGE_MODEL="${AGENT_FORGE_MODEL:-${DEEPSEEK_MODEL:-deepseek-v4-flash}}"

# shellcheck disable=SC1091
source .venv/bin/activate

cleanup_fixture() {
  if [ "${KEEP_PATCH:-0}" = "1" ]; then
    return 0
  fi
  python - <<'PY'
from pathlib import Path

Path("examples/demo_repo/src/calculator.py").write_text(
    "def add(a: int, b: int) -> int:\n    return a + b\n",
    encoding="utf-8",
)
PY
}

trap cleanup_fixture EXIT

OUTPUT_DIR="${OUTPUT_DIR:-.agent_forge/latest/single-deepseek}"
TRACE_FILE="${TRACE_FILE:-${OUTPUT_DIR}/trace.json}"
mkdir -p "$(dirname "${TRACE_FILE}")"

# Positional arguments are forwarded to run_demo.py, so you can still override
# runtime knobs without editing the script, for example:
#   TRACE_FILE=trace-x.json local_scripts/run_deepseek.sh --max-steps 8
python run_demo.py \
  --mode single \
  --llm deepseek \
  --trace-file "${TRACE_FILE}" \
  "$@"

TRACE_DIR="$(dirname "${TRACE_FILE}")"
TRACE_NAME="$(basename "${TRACE_FILE}")"
if [ "${TRACE_NAME}" = "trace.json" ]; then
  USAGE_JSON="${TRACE_DIR}/usage.json"
  USAGE_REPORT="${TRACE_DIR}/usage_report.md"
else
  USAGE_JSON="${TRACE_FILE%.json}.usage.json"
  USAGE_REPORT="${TRACE_FILE%.json}.usage_report.md"
fi

# Keep only the files a human usually needs from this shortcut.
rm -f "${USAGE_JSON}"

echo "Main report: ${PROJECT_DIR}/${USAGE_REPORT}"
echo "Raw trace:   ${PROJECT_DIR}/${TRACE_FILE}"

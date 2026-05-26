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
#   trace-deepseek.json              compact machine-readable trace
#   trace-deepseek.pretty.json       formatted trace for human reading
#   trace-deepseek.usage.json        token/cost/context/tool metrics
#   trace-deepseek.usage_report.md   readable engineering usage report

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

TRACE_FILE="${TRACE_FILE:-trace-deepseek.json}"

# Positional arguments are forwarded to run_demo.py, so you can still override
# runtime knobs without editing the script, for example:
#   TRACE_FILE=trace-x.json local_scripts/run_deepseek.sh --max-steps 8
python run_demo.py \
  --mode single \
  --llm deepseek \
  --trace-file "${TRACE_FILE}" \
  "$@"

python -m json.tool "${TRACE_FILE}" > "${TRACE_FILE%.json}.pretty.json"

echo "Wrote ${PROJECT_DIR}/${TRACE_FILE}"
echo "Wrote ${PROJECT_DIR}/${TRACE_FILE%.json}.pretty.json"
echo "Wrote ${PROJECT_DIR}/${TRACE_FILE%.json}.usage.json"
echo "Wrote ${PROJECT_DIR}/${TRACE_FILE%.json}.usage_report.md"

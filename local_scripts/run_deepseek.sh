#!/usr/bin/env bash
set -Eeuo pipefail

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

# DeepSeek's official Chat Completions endpoint is OpenAI-compatible. Keep the
# defaults here so personal Mac runs are one command, while company machines can
# keep using local_scripts/run_mock.sh with no external network.
export AGENT_FORGE_BASE_URL="${AGENT_FORGE_BASE_URL:-${DEEPSEEK_BASE_URL:-https://api.deepseek.com}}"
export AGENT_FORGE_API_KEY="${AGENT_FORGE_API_KEY:-${DEEPSEEK_API_KEY:-}}"
export AGENT_FORGE_MODEL="${AGENT_FORGE_MODEL:-${DEEPSEEK_MODEL:-deepseek-v4-flash}}"

# shellcheck disable=SC1091
source .venv/bin/activate

TRACE_FILE="${TRACE_FILE:-trace-deepseek.json}"

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

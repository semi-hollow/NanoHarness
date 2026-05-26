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

# This is the primary personal-Mac entrypoint: real DeepSeek model plus the
# WebhookPatchBench scenario. The generic run_webhook_bench.sh remains useful
# for company/offline MockLLM runs.
export AGENT_FORGE_WEBHOOK_LLM="deepseek"
export AGENT_FORGE_BASE_URL="${AGENT_FORGE_BASE_URL:-${DEEPSEEK_BASE_URL:-https://api.deepseek.com}}"
export AGENT_FORGE_API_KEY="${AGENT_FORGE_API_KEY:-${DEEPSEEK_API_KEY:-}}"
export AGENT_FORGE_MODEL="${AGENT_FORGE_MODEL:-${DEEPSEEK_MODEL:-deepseek-v4-flash}}"
export TRACE_FILE="${TRACE_FILE:-trace-webhook-deepseek.json}"

"${SCRIPT_DIR}/run_webhook_bench.sh" "$@"

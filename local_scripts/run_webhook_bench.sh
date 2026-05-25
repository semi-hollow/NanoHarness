#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

if [ ! -f ".venv/bin/activate" ]; then
  echo "Missing .venv. Run scripts/setup_macos_local.sh first." >&2
  exit 1
fi

# Default to MockLLM so this benchmark works on company/offline machines.
# Personal runs can use DeepSeek by setting AGENT_FORGE_WEBHOOK_LLM=deepseek.
WEBHOOK_LLM="${AGENT_FORGE_WEBHOOK_LLM:-mock}"
TRACE_FILE="${TRACE_FILE:-trace-webhook.json}"

# shellcheck disable=SC1091
source .venv/bin/activate

python run_demo.py \
  "Resolve examples/webhook_service_repo/issues/issue_001_duplicate_webhook.md" \
  --mode single \
  --llm "${WEBHOOK_LLM}" \
  --workspace . \
  --trace-file "${TRACE_FILE}" \
  --max-steps 9 \
  --max-context-chars 7000 \
  "$@"

python -m json.tool "${TRACE_FILE}" > "${TRACE_FILE%.json}.pretty.json"

echo "Wrote ${PROJECT_DIR}/${TRACE_FILE}"
echo "Wrote ${PROJECT_DIR}/${TRACE_FILE%.json}.pretty.json"
echo "Wrote ${PROJECT_DIR}/${TRACE_FILE%.json}.usage.json"
echo "Wrote ${PROJECT_DIR}/${TRACE_FILE%.json}.usage_report.md"

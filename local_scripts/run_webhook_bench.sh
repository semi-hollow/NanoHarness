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
MAX_STEPS="${MAX_STEPS:-12}"
MAX_CONTEXT_CHARS="${MAX_CONTEXT_CHARS:-7000}"
TASK=$'Resolve examples/webhook_service_repo/issues/issue_001_duplicate_webhook.md.\n\nRequirements:\n- Preserve signature verification before any side effect.\n- Add duplicate event_id handling before store.insert_event and queue.enqueue.\n- Do not read secret files.\n- Do not modify docs/security_policy.md.\n- Validate with exactly this allowed command: python -m unittest discover examples/webhook_service_repo/tests\n- Do not use pytest, cd, python -c, or direct test-file execution; those are intentionally blocked by command policy.\n- Stop only after the allowed unittest command succeeds.'

# shellcheck disable=SC1091
source .venv/bin/activate

python run_demo.py \
  "${TASK}" \
  --mode single \
  --llm "${WEBHOOK_LLM}" \
  --workspace . \
  --trace-file "${TRACE_FILE}" \
  --max-steps "${MAX_STEPS}" \
  --max-context-chars "${MAX_CONTEXT_CHARS}" \
  "$@"

python -m json.tool "${TRACE_FILE}" > "${TRACE_FILE%.json}.pretty.json"

echo "Wrote ${PROJECT_DIR}/${TRACE_FILE}"
echo "Wrote ${PROJECT_DIR}/${TRACE_FILE%.json}.pretty.json"
echo "Wrote ${PROJECT_DIR}/${TRACE_FILE%.json}.usage.json"
echo "Wrote ${PROJECT_DIR}/${TRACE_FILE%.json}.usage_report.md"

#!/usr/bin/env bash
set -Eeuo pipefail

# Purpose:
#   Run the main WebhookPatchBench scenario with the real DeepSeek API.
#
# Why this is the primary script:
#   The old calculator demo only proves "read -> patch -> test" on a tiny file.
#   WebhookPatchBench is still small, but it exercises the important CodingAgent
#   runtime pieces together: context selection, issue reading, guarded patching,
#   command policy, test execution, trace, usage, and recovery signals.
#
# Output:
#   trace-webhook-deepseek.json              compact machine-readable trace
#   trace-webhook-deepseek.pretty.json       formatted trace for human reading
#   trace-webhook-deepseek.usage.json        token/cost/context/tool metrics
#   trace-webhook-deepseek.usage_report.md   readable engineering usage report

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

# DeepSeek's Chat Completions endpoint is OpenAI-compatible. These are consumed
# by agent_forge.models.ModelGateway after run_demo.py resolves --llm deepseek.
export AGENT_FORGE_BASE_URL="${AGENT_FORGE_BASE_URL:-${DEEPSEEK_BASE_URL:-https://api.deepseek.com}}"
export AGENT_FORGE_API_KEY="${AGENT_FORGE_API_KEY:-${DEEPSEEK_API_KEY:-}}"
export AGENT_FORGE_MODEL="${AGENT_FORGE_MODEL:-${DEEPSEEK_MODEL:-deepseek-v4-flash}}"

# These knobs are intentionally visible here because they are the first things
# you tune when a real model needs more turns or less prompt budget.
TRACE_FILE="${TRACE_FILE:-trace-webhook-deepseek.json}"
MAX_STEPS="${MAX_STEPS:-12}"
MAX_CONTEXT_CHARS="${MAX_CONTEXT_CHARS:-7000}"

# The task text is part of the benchmark. It tells the model which issue to fix
# and which commands are allowed. Keeping this explicit makes command-policy
# failures explainable when reading trace-webhook-deepseek.pretty.json.
TASK=$'Resolve examples/webhook_service_repo/issues/issue_001_duplicate_webhook.md.\n\nRequirements:\n- Preserve signature verification before any side effect.\n- Add duplicate event_id handling before store.insert_event and queue.enqueue.\n- Do not read secret files.\n- Do not modify docs/security_policy.md.\n- Validate with exactly this allowed command: python -m unittest discover examples/webhook_service_repo/tests\n- Do not use pytest, cd, python -c, or direct test-file execution; those are intentionally blocked by command policy.\n- Stop only after the allowed unittest command succeeds.'

# shellcheck disable=SC1091
source .venv/bin/activate

python run_demo.py \
  "${TASK}" \
  --mode single \
  --llm deepseek \
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

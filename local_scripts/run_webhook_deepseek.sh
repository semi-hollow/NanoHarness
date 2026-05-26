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
#   .agent_forge/latest/webhook-deepseek/trace.json
#       Raw event stream. Open it only when you need exact step-by-step evidence.
#   .agent_forge/latest/webhook-deepseek/usage_report.md
#       Main file to read. It summarizes tokens, cost, context, and tool calls.
#
# By default the benchmark fixture is restored after the run so Git stays clean.
# To keep the model's patch in examples/webhook_service_repo for inspection:
#   KEEP_PATCH=1 local_scripts/run_webhook_deepseek.sh

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
OUTPUT_DIR="${OUTPUT_DIR:-.agent_forge/latest/webhook-deepseek}"
TRACE_FILE="${TRACE_FILE:-${OUTPUT_DIR}/trace.json}"
MAX_STEPS="${MAX_STEPS:-12}"
MAX_CONTEXT_CHARS="${MAX_CONTEXT_CHARS:-7000}"
mkdir -p "$(dirname "${TRACE_FILE}")"

# The task text is part of the benchmark. It tells the model which issue to fix
# and which commands are allowed. Keeping this explicit makes command-policy
# failures explainable when reading the raw trace.json.
TASK=$'Resolve examples/webhook_service_repo/issues/issue_001_duplicate_webhook.md.\n\nRequirements:\n- Preserve signature verification before any side effect.\n- Add duplicate event_id handling before store.insert_event and queue.enqueue.\n- Do not read secret files.\n- Do not modify docs/security_policy.md.\n- Validate with exactly this allowed command: python -m unittest discover examples/webhook_service_repo/tests\n- Do not use pytest, cd, python -c, or direct test-file execution; those are intentionally blocked by command policy.\n- Stop only after the allowed unittest command succeeds.'

# shellcheck disable=SC1091
source .venv/bin/activate

cleanup_fixture() {
  if [ "${KEEP_PATCH:-0}" = "1" ]; then
    return 0
  fi
  python - <<'PY'
from pathlib import Path

from agent_forge.cli import reset_webhook_bench

reset_webhook_bench(".")
Path("examples/demo_repo/src/calculator.py").write_text(
    "def add(a: int, b: int) -> int:\n    return a + b\n",
    encoding="utf-8",
)
PY
}

trap cleanup_fixture EXIT

python run_demo.py \
  "${TASK}" \
  --mode single \
  --llm deepseek \
  --workspace . \
  --trace-file "${TRACE_FILE}" \
  --max-steps "${MAX_STEPS}" \
  --max-context-chars "${MAX_CONTEXT_CHARS}" \
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

# run_demo.py writes usage.json for machines. The normal learning path only
# needs the markdown report, so remove the extra JSON to keep the tree quiet.
rm -f "${USAGE_JSON}"

echo "Main report: ${PROJECT_DIR}/${USAGE_REPORT}"
echo "Raw trace:   ${PROJECT_DIR}/${TRACE_FILE}"

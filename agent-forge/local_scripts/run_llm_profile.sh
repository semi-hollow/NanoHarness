#!/usr/bin/env bash
set -Eeuo pipefail

PROFILE="${1:-ollama-qwen}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

if [ ! -f ".venv/bin/activate" ]; then
  echo "Missing .venv. Run scripts/setup_macos_local.sh or scripts/setup_wsl_local.sh first." >&2
  exit 1
fi

if [ ! -f "llm_profiles.json" ]; then
  echo "Missing llm_profiles.json. Copy llm_profiles.example.json and fill your local settings." >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python run_demo.py \
  --mode single \
  --llm-profile "${PROFILE}" \
  --trace-file "trace-${PROFILE}.json"

python -m json.tool "trace-${PROFILE}.json" > "trace-${PROFILE}.pretty.json"

echo "Wrote ${PROJECT_DIR}/trace-${PROFILE}.json"
echo "Wrote ${PROJECT_DIR}/trace-${PROFILE}.pretty.json"

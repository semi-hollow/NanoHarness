#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

if [ ! -f ".venv/bin/activate" ]; then
  echo "Missing .venv. Run scripts/setup_macos_local.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

export AGENT_FORGE_BASE_URL="${AGENT_FORGE_BASE_URL:-http://localhost:11434/v1}"
export AGENT_FORGE_API_KEY="${AGENT_FORGE_API_KEY:-ollama}"
export AGENT_FORGE_MODEL="${AGENT_FORGE_MODEL:-qwen2.5-coder:7b}"

python run_demo.py --mode single --llm openai --trace-file trace-ollama.json
python -m json.tool trace-ollama.json > trace-ollama.pretty.json

echo "Wrote ${PROJECT_DIR}/trace-ollama.json"
echo "Wrote ${PROJECT_DIR}/trace-ollama.pretty.json"

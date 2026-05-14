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

python run_demo.py --mode single --trace-file trace-mock.json
python -m json.tool trace-mock.json > trace-mock.pretty.json

echo "Wrote ${PROJECT_DIR}/trace-mock.json"
echo "Wrote ${PROJECT_DIR}/trace-mock.pretty.json"

#!/usr/bin/env bash
set -euo pipefail

# Double-click launcher for the local browser workbench on macOS.
# It keeps setup local to this repository and then opens http://127.0.0.1:8765.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x ".venv/bin/python" ]]; then
  echo "[setup] creating .venv"
  python3 -m venv .venv
  .venv/bin/python -m pip install -U pip setuptools wheel
  .venv/bin/python -m pip install -e '.[bench]'
fi

echo "[start] Agent Forge Workbench"
echo "[path]  $ROOT_DIR"
echo "[url]   http://127.0.0.1:8765"
echo
echo "Close this Terminal window or press Ctrl+C to stop the local server."

.venv/bin/python -m agent_forge ui

#!/usr/bin/env bash
set -Eeuo pipefail

# Purpose:
#   Bootstrap this repo on a personal macOS machine.
#
# What it does:
#   1. Finds the NanoHarness project root from any child directory.
#   2. Creates/reuses .venv.
#   3. Installs the project in editable mode.
#   4. Runs scripts/verify.sh once as a deterministic local health check.
#
# What it does not do:
#   It does not store API keys, modify global Python, or call online LLMs.

LOG_FILE="${HOME}/agent_forge_macos_setup.log"

log() {
  printf '%s\n' "$*"
}

die() {
  local exit_code="${1:-1}"
  log ""
  log "Setup failed. Last 120 lines from ${LOG_FILE}:"
  if [ -f "${LOG_FILE}" ]; then
    tail -n 120 "${LOG_FILE}"
  else
    log "No log file was created."
  fi
  exit "${exit_code}"
}

run() {
  log ""
  log "+ $*"
  "$@"
}

find_project_dir() {
  # The repo used to have a nested layout, so this finder intentionally checks
  # the current directory, parents, and the script's parent. The canonical root
  # today is the directory containing pyproject.toml and agent_forge/.
  local start_dir
  start_dir="$(pwd)"
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  if [ -f "${start_dir}/pyproject.toml" ] && [ -d "${start_dir}/agent_forge" ]; then
    printf '%s\n' "${start_dir}"
    return 0
  fi

  local probe="${start_dir}"
  while [ "${probe}" != "/" ]; do
    if [ -f "${probe}/pyproject.toml" ] && [ -d "${probe}/agent_forge" ]; then
      printf '%s\n' "${probe}"
      return 0
    fi
    probe="$(dirname "${probe}")"
  done

  if [ -f "${script_dir}/../pyproject.toml" ] && [ -d "${script_dir}/../agent_forge" ]; then
    cd "${script_dir}/.." >/dev/null 2>&1
    pwd
    return 0
  fi

  return 1
}

choose_python() {
  # Prefer Python 3.11 because earlier project scripts referenced python3.11,
  # but accept any Python >= 3.10 so a normal macOS/Homebrew setup works.
  for candidate in python3.11 python3.12 python3.10 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      if "${candidate}" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
      then
        command -v "${candidate}"
        return 0
      fi
    fi
  done
  return 1
}

ensure_setuptools_find_config() {
  # Editable install fails in a flat repo unless setuptools is told that only
  # agent_forge is a package. This block is idempotent and never appends twice.
  if grep -q '^\[tool\.setuptools\.packages\.find\]' pyproject.toml; then
    log "pyproject.toml already has setuptools package discovery config."
    return 0
  fi

  log "Appending setuptools package discovery config to pyproject.toml."
  {
    printf '\n'
    printf '[tool.setuptools.packages.find]\n'
    printf 'include = ["agent_forge*"]\n'
    printf 'exclude = ["tests*", "tutorials*", "examples*", "docs*", "scripts*"]\n'
  } >> pyproject.toml
}

main() {
  : > "${LOG_FILE}"
  exec > >(tee -a "${LOG_FILE}") 2>&1
  trap 'die $?' ERR

  log "Agent Forge macOS setup"
  log "Started at: $(date)"
  log "Initial directory: $(pwd)"
  log ""
  log "Environment check:"
  log "  pwd: $(pwd)"
  log "  uname: $(uname -a)"
  log "  python3: $(command -v python3 || true)"
  if command -v python3 >/dev/null 2>&1; then
    log "  python3 version: $(python3 --version 2>&1)"
  fi
  log "  python3.11: $(command -v python3.11 || true)"
  if command -v python3.11 >/dev/null 2>&1; then
    log "  python3.11 version: $(python3.11 --version 2>&1)"
  fi
  log "  pip3: $(command -v pip3 || true)"
  if command -v pip3 >/dev/null 2>&1; then
    log "  pip3 version: $(pip3 --version 2>&1)"
  fi
  log "  brew: $(command -v brew || true)"
  log "  jq: $(command -v jq || true)"

  PROJECT_DIR="$(find_project_dir)" || {
    log "Could not find the NanoHarness project root from $(pwd)."
    log "Run this script from NanoHarness or any child directory inside the project."
    exit 1
  }
  cd "${PROJECT_DIR}"
  log ""
  log "Project directory: ${PROJECT_DIR}"

  PYTHON_BIN="$(choose_python)" || {
    log "Python >= 3.10 was not found. Install Python 3.11 first, for example with Homebrew: brew install python@3.11"
    exit 1
  }
  log "Selected Python: ${PYTHON_BIN}"
  run "${PYTHON_BIN}" -m venv .venv

  # shellcheck disable=SC1091
  source .venv/bin/activate
  log "Venv Python: $(command -v python)"
  log "Venv version: $(python --version 2>&1)"

  log ""
  log "+ python -m pip install -U pip setuptools wheel"
  if ! python -m pip install -U pip setuptools wheel; then
    log "pip/setuptools/wheel upgrade failed; continuing because editable install may still work with existing cached tooling."
  fi

  ensure_setuptools_find_config

  log "+ python -m pip install -e '.[bench]'"
  if ! python -m pip install -e '.[bench]'; then
    log "Benchmark extras failed to install; falling back to core editable install."
    run python -m pip install -e .
  fi

  if command -v python3.11 >/dev/null 2>&1; then
    log "System python3.11 is available: $(command -v python3.11)"
  else
    log "System python3.11 is not available; creating .venv/bin/python3.11 compatibility symlink."
    ln -sf "$(command -v python)" .venv/bin/python3.11
  fi

  if [ ! -x scripts/verify.sh ]; then
    chmod +x scripts/verify.sh
  fi

  run scripts/verify.sh

  log ""
  log "Setup succeeded."
  log "Project path: ${PROJECT_DIR}"
  log "Venv python: ${PROJECT_DIR}/.venv/bin/python"
  log ""
  log "Daily commands:"
  log "  cd \"${PROJECT_DIR}\""
  log "  source .venv/bin/activate"
  log "  forge doctor"
  log "  forge bench swebench --limit 1 --provider deepseek --direct-baseline"
  log "  forge report latest"
  log "  scripts/verify.sh"
}

main "$@"

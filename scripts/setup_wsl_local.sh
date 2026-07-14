#!/usr/bin/env bash
set -Eeuo pipefail

# 用途：
#   在 Windows WSL/Ubuntu 中初始化本仓库。
#
# 本脚本与 macOS 初始化脚本保持同样流程，但提供适合 WSL 的依赖提示。安装完成后默认离线；
# 只有配置 DEEPSEEK_API_KEY 时，验证阶段才运行真实模型冒烟测试。

LOG_FILE="${HOME}/agent_forge_wsl_setup.log"

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
  # 从任意子目录定位同时包含 pyproject.toml 和 agent_forge/ 的项目根目录。
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
  # 优先使用 Python 3.11；本项目也支持 Python 3.10 及以上版本。
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
  # 仓库根目录同时包含 docs、tests 和 agent_forge，因此 editable install 需要显式包发现。
  if grep -q '^\[tool\.setuptools\.packages\.find\]' pyproject.toml; then
    log "pyproject.toml already has setuptools package discovery config."
    return 0
  fi

  log "Appending setuptools package discovery config to pyproject.toml."
  {
    printf '\n'
    printf '[tool.setuptools.packages.find]\n'
    printf 'include = ["agent_forge*"]\n'
    printf 'exclude = ["tests*", "docs*", "scripts*"]\n'
  } >> pyproject.toml
}

main() {
  : > "${LOG_FILE}"
  exec > >(tee -a "${LOG_FILE}") 2>&1
  trap 'die $?' ERR

  log "Agent Forge WSL setup"
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
  log "  jq: $(command -v jq || true)"

  PROJECT_DIR="$(find_project_dir)" || {
    log "Could not find the Agent Forge project root from $(pwd)."
    log "Run this script from the Agent Forge project root or any child directory inside the project."
    exit 1
  }
  cd "${PROJECT_DIR}"
  log ""
  log "Project directory: ${PROJECT_DIR}"

  PYTHON_BIN="$(choose_python)" || {
    log "Python >= 3.10 was not found."
    log "Install dependencies first, e.g.:"
    log "  sudo apt update"
    log "  sudo apt install -y git curl build-essential python3 python3-venv python3-pip rsync jq"
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
    log "pip/setuptools/wheel upgrade failed; continuing with existing tooling."
  fi

  ensure_setuptools_find_config
  log "+ python -m pip install -e '.[bench,dev]'"
  if ! python -m pip install -e '.[bench,dev]'; then
    log "Benchmark extras failed to install; falling back to core editable install."
    run python -m pip install -e .
  fi

  if ! command -v python3.11 >/dev/null 2>&1; then
    log "System python3.11 is not available; creating .venv/bin/python3.11 compatibility symlink."
    ln -sf "$(command -v python)" .venv/bin/python3.11
  fi

  chmod +x scripts/verify.sh
  run scripts/verify.sh

  log ""
  log "Setup succeeded."
  log "Project path: ${PROJECT_DIR}"
  log "Venv python: ${PROJECT_DIR}/.venv/bin/python"
  log "Daily commands:"
  log "  cd \"${PROJECT_DIR}\""
  log "  source .venv/bin/activate"
  log "  forge doctor"
  log "  forge run \"阅读这个项目结构并说明入口，不要修改文件\" --provider deepseek"
  log "  scripts/verify.sh"
}

main "$@"

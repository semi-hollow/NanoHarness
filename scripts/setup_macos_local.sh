#!/usr/bin/env bash
set -Eeuo pipefail

# 用途：
#   在个人 macOS 机器上初始化本仓库。
#
# 执行内容：
#   1. 从任意子目录定位 Agent Forge 项目根目录。
#   2. 创建或复用 .venv。
#   3. 以 editable 模式安装项目。
#   4. 运行 scripts/verify.sh，完成一次确定性本地健康检查。
#
# 不执行的内容：
#   不保存 API key，不修改全局 Python，也不调用在线模型。

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
  # 仓库曾使用嵌套目录，因此这里会检查当前目录、父目录和脚本父目录；规范根目录必须同时
  # 包含 pyproject.toml 和 agent_forge/。
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
  # 优先使用旧脚本曾引用的 Python 3.11，同时接受 Python 3.10 及以上版本。
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
  # 平铺仓库需要显式告诉 setuptools 只发现 agent_forge；该步骤可重复执行且不会重复追加。
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
    log "Could not find the Agent Forge project root from $(pwd)."
    log "Run this script from the Agent Forge project root or any child directory inside the project."
    exit 1
  }
  cd "${PROJECT_DIR}"
  log ""
  log "Project directory: ${PROJECT_DIR}"

  PYTHON_BIN="$(choose_python)" || {
    log "Python >= 3.10 was not found. Install Python 3.11 first, e.g. with Homebrew: brew install python@3.11"
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

  log "+ python -m pip install -e '.[bench,dev]'"
  if ! python -m pip install -e '.[bench,dev]'; then
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

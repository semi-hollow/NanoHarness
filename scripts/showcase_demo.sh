#!/usr/bin/env bash
set -Eeuo pipefail

# 统一编排正式 Debug Lab 与只读 Workbench，不复制 Runtime、fixture 或凭据逻辑。
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${ROOT_DIR}"

if [[ ! -x .venv/bin/python ]]; then
  printf 'Missing .venv. Complete the README Quick Start once before opening the Workbench.\n' >&2
  exit 2
fi

scenario="governed"
produce_evidence=true
stay_attached=false
require_evidence=true
workbench_source="governed"
pointer_path=".agent_forge/internal/index/run.txt"
status_evidence_field="latest_run"
case "${1:-}" in
  "") ;;
  --show-governed)
    scenario="show-governed"
    produce_evidence=false
    ;;
  --show-coordinated)
    scenario="show-coordinated"
    produce_evidence=false
    workbench_source="orchestration"
    ;;
  --serve)
    scenario="serve"
    produce_evidence=false
    stay_attached=true
    require_evidence=false
    workbench_source=""
    ;;
  *)
    printf 'Usage: scripts/showcase_demo.sh [--show-governed|--show-coordinated|--serve]\n' >&2
    exit 2
    ;;
esac

if [[ "${produce_evidence}" == true ]]; then
  printf '\n=== NanoHarness: produce governed Evidence ===\n'
  # 外层脚本统一等待 Workbench 就绪并打开 Chrome，禁止内层 Lab 重复弹页。
  .venv/bin/python examples/debug_lab/run.py "${scenario}" --no-open-workbench
else
  printf '\n=== NanoHarness: open the latest published Evidence ===\n'
fi

state_dir=".agent_forge/internal/debug-lab/state"
mkdir -p "${state_dir}"
# Workbench 使用同一个稳定 URL；这个小指针只决定首页默认选中哪次运行。
if [[ -n "${workbench_source}" ]]; then
  printf '%s\n' "${workbench_source}" >"${state_dir}/workbench_source.txt"
fi
printf '\n=== NanoHarness: open the same Evidence in read-only Workbench ===\n'
expected_project="$(pwd -P)"
expected_workbench_source="$(
  .venv/bin/python -c \
    'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path("apps/workbench/presentation/http.py").read_bytes()).hexdigest())'
)"
expected_evidence=""
if [[ "${require_evidence}" == true ]]; then
  if [[ ! -f "${pointer_path}" ]]; then
    printf 'No published Evidence found at %s. Run the matching Lab once first.\n' "${pointer_path}" >&2
    exit 2
  fi
  expected_evidence="$(.venv/bin/python -c 'import os,pathlib,sys; print(os.path.realpath(pathlib.Path(sys.argv[1]).read_text().strip()))' "${pointer_path}")"
fi
port=8765
status_json=""
if [[ -f "${state_dir}/workbench.port" ]]; then
  saved_port="$(tr -cd '0-9' <"${state_dir}/workbench.port")"
  if [[ -n "${saved_port}" ]]; then
    saved_status="$(curl --silent --fail "http://127.0.0.1:${saved_port}/api/status" 2>/dev/null || true)"
    saved_project="$(printf '%s' "${saved_status}" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin).get("project_dir", ""))' 2>/dev/null || true)"
    if [[ -n "${saved_project}" ]] && \
       [[ "$(.venv/bin/python -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${saved_project}")" == "${expected_project}" ]]; then
      port="${saved_port}"
      status_json="${saved_status}"
    fi
  fi
fi
if [[ -z "${status_json}" ]]; then
  status_json="$(curl --silent --fail "http://127.0.0.1:${port}/api/status" 2>/dev/null || true)"
fi
running_project="$(printf '%s' "${status_json}" | .venv/bin/python -c 'import json,sys; data=json.load(sys.stdin) if sys.stdin.readable() else {}; print(data.get("project_dir", ""))' 2>/dev/null || true)"
running_workbench_source="$(
  printf '%s' "${status_json}" |
    .venv/bin/python -c \
      'import json,sys; print(json.load(sys.stdin).get("workbench_source_sha256", ""))' \
      2>/dev/null || true
)"

# 已启动的 Python 进程不会热加载 Workbench 源码。指纹不一致时，只替换本脚本记录的进程。
if [[ -n "${running_project}" ]] && \
   [[ "$( .venv/bin/python -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${running_project}")" == "${expected_project}" ]] && \
   [[ "${running_workbench_source}" != "${expected_workbench_source}" ]]; then
  managed_pid="$(tr -cd '0-9' <"${state_dir}/workbench.pid" 2>/dev/null || true)"
  if [[ -n "${managed_pid}" ]] && kill -0 "${managed_pid}" 2>/dev/null; then
    kill "${managed_pid}" 2>/dev/null || true
    for _ in {1..40}; do
      if ! kill -0 "${managed_pid}" 2>/dev/null; then
        break
      fi
      sleep 0.05
    done
  fi
  status_json=""
  if curl --silent --fail "http://127.0.0.1:${port}/api/status" >/dev/null 2>&1; then
    port="$(.venv/bin/python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  fi
fi

if [[ -n "${status_json}" ]] && [[ -z "${running_project}" ]]; then
  port="$(.venv/bin/python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  status_json=""
elif [[ -n "${running_project}" ]] && \
     [[ "$(.venv/bin/python -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${running_project}")" != "${expected_project}" ]]; then
  port="$(.venv/bin/python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  status_json=""
fi

if [[ -z "${status_json}" ]]; then
  if [[ "${stay_attached}" == true ]]; then
    .venv/bin/python -m agent_forge ui --no-open --port "${port}" \
      >"${state_dir}/workbench.log" 2>&1 &
  else
    nohup .venv/bin/python -m agent_forge ui --no-open --port "${port}" \
      >"${state_dir}/workbench.log" 2>&1 &
  fi
  workbench_pid="$!"
  printf '%s\n' "${workbench_pid}" >"${state_dir}/workbench.pid"
  printf '%s\n' "${port}" >"${state_dir}/workbench.port"
fi

ready=false
for _ in {1..40}; do
  status_json="$(curl --silent --fail "http://127.0.0.1:${port}/api/status" 2>/dev/null || true)"
  if [[ -n "${status_json}" ]]; then
    status_pair="$(printf '%s' "${status_json}" | .venv/bin/python -c 'import json,os,sys; d=json.load(sys.stdin); print(os.path.realpath(d.get("project_dir", ""))); print(os.path.realpath(d.get(sys.argv[1], "")))' "${status_evidence_field}" 2>/dev/null || true)"
    status_project="$(printf '%s\n' "${status_pair}" | sed -n '1p')"
    status_evidence="$(printf '%s\n' "${status_pair}" | sed -n '2p')"
    if [[ "${status_project}" == "${expected_project}" ]] && \
       { [[ "${require_evidence}" != true ]] || [[ "${status_evidence}" == "${expected_evidence}" ]]; }; then
      ready=true
      break
    fi
  fi
  sleep 0.25
done

if [[ "${ready}" != true ]]; then
  printf 'Workbench did not expose the new Evidence. See %s/workbench.log\n' "${state_dir}" >&2
  exit 1
fi
workbench_url="http://127.0.0.1:${port}/?focus=1"
printf 'WORKBENCH: %s\n' "${workbench_url}"
if [[ -d "/Applications/Google Chrome.app" ]]; then
  open -a "Google Chrome" "${workbench_url}"
else
  # 没有安装 Chrome 时才回退到系统默认浏览器。
  open "${workbench_url}"
fi

printf '%s\n' \
  'Learning path: Governed Control Plane -> Coordinated Agents -> Mini-50 Repository Repair' \
  'Boundary: Workbench reads Evidence; Harness/CLI owns execution.'

if [[ "${stay_attached}" == true ]] && [[ -n "${workbench_pid:-}" ]]; then
  printf 'Workbench stays attached to this Run Configuration; click Stop to close it.\n'
  cleanup_workbench() {
    kill "${workbench_pid}" 2>/dev/null || true
  }
  trap cleanup_workbench EXIT INT TERM
  wait "${workbench_pid}" || true
fi

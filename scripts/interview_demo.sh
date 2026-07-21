#!/usr/bin/env bash
set -Eeuo pipefail

# 面试只编排正式 Debug Lab 与只读 Workbench，不复制 Runtime、fixture 或凭据逻辑。
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${ROOT_DIR}"

if [[ ! -x .venv/bin/python ]]; then
  scripts/setup_macos_local.sh --quick
fi

scenario="control"
case "${1:-}" in
  "") ;;
  --live) scenario="live" ;;
  --show-live) scenario="show-live" ;;
  --show-astropy) scenario="show-astropy" ;;
  *)
    printf 'Usage: scripts/interview_demo.sh [--live|--show-live|--show-astropy]\n' >&2
    exit 2
    ;;
esac

printf '\n=== NanoHarness: produce governed Evidence ===\n'
.venv/bin/python examples/debug_lab/run.py "${scenario}"

state_dir=".agent_forge/debug-lab/state"
mkdir -p "${state_dir}"
printf '\n=== NanoHarness: open the same Evidence in read-only Workbench ===\n'
expected_project="$(pwd -P)"
pointer_name="bench.txt"
if [[ "${scenario}" != "show-astropy" ]]; then
  pointer_name="run.txt"
fi
expected_run="$(.venv/bin/python -c 'import os,pathlib,sys; print(os.path.realpath(pathlib.Path(".agent_forge/latest", sys.argv[1]).read_text().strip()))' "${pointer_name}")"
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

if [[ -n "${status_json}" ]] && [[ -z "${running_project}" ]]; then
  port="$(.venv/bin/python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  status_json=""
elif [[ -n "${running_project}" ]] && \
     [[ "$(.venv/bin/python -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${running_project}")" != "${expected_project}" ]]; then
  port="$(.venv/bin/python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
  status_json=""
fi

if [[ -z "${status_json}" ]]; then
  nohup .venv/bin/python -m agent_forge ui --no-open --port "${port}" \
    >"${state_dir}/workbench.log" 2>&1 &
  printf '%s\n' "$!" >"${state_dir}/workbench.pid"
  printf '%s\n' "${port}" >"${state_dir}/workbench.port"
fi

ready=false
for _ in {1..40}; do
  status_json="$(curl --silent --fail "http://127.0.0.1:${port}/api/status" 2>/dev/null || true)"
  if [[ -n "${status_json}" ]]; then
    status_pair="$(printf '%s' "${status_json}" | .venv/bin/python -c 'import json,os,sys; d=json.load(sys.stdin); print(os.path.realpath(d.get("project_dir", ""))); print(os.path.realpath(d.get("latest_run", "")))' 2>/dev/null || true)"
    status_project="$(printf '%s\n' "${status_pair}" | sed -n '1p')"
    status_run="$(printf '%s\n' "${status_pair}" | sed -n '2p')"
    if [[ "${status_project}" == "${expected_project}" ]] && [[ "${status_run}" == "${expected_run}" ]]; then
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
open "http://127.0.0.1:${port}/?focus=1"

printf '%s\n' \
  'Narrative: Run Story -> Timeline -> Approval/Checkpoint -> Artifacts -> Claim Ladder' \
  'Boundary: Workbench reads Evidence; Harness/CLI owns execution.'

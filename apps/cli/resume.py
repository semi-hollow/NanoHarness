"""``forge resume``：从 durable state 构造新的显式 run。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from apps.run_composition import build_single_harness
from apps.run_configuration import CONFIG_SCHEMA_VERSION
from agent_forge.observability.api import refresh_run_manifest
from agent_forge.runtime.api import (
    HumanInputResponseCommand,
    decide_approval,
    latest_checkpoint_path,
    list_pending_approvals,
    list_pending_human_inputs,
    load_task_checkpoint,
    respond_to_human_input,
)
from agent_forge.runtime.domain.task import TaskCheckpoint
from agent_forge.infrastructure.storage_layout import APPROVAL_ROOT, HUMAN_INPUT_ROOT

_CONTINUATION_OWNED_CONFIG = {
    "resume_state",
    "runtime_instructions_configured",
    "runtime_instructions_sha256",
    "task",
    "workspace",
}


# region 1. Single-Agent continuation：从 durable checkpoint 继续同一 Turn
def resume_repository_task(args: argparse.Namespace) -> Path:
    """加载 checkpoint/HITL 状态并启动新的 continuation run。"""

    _reject_multi_agent_run(args.run_dir)
    checkpoint_path = latest_checkpoint_path(args.run_dir)
    checkpoint = load_task_checkpoint(checkpoint_path)
    _inherit_resolved_config(args)
    human_input_root = _control_root(
        args.human_input_root or str(HUMAN_INPUT_ROOT),
        checkpoint,
    )
    approval_root = _control_root(
        args.approval_root or str(APPROVAL_ROOT),
        checkpoint,
    )
    _persist_operator_decision(
        args,
        checkpoint=checkpoint,
        human_input_root=human_input_root,
        approval_root=approval_root,
    )
    if getattr(args, "task", ""):
        raise SystemExit(
            "resume cannot override Turn.root_task; send a follow-up as a new Turn"
        )
    requested_override = str(getattr(args, "workspace", "") or "")
    if requested_override and Path(requested_override).expanduser().resolve() != Path(
        checkpoint.workspace
    ).resolve():
        raise SystemExit("resume workspace does not match checkpoint Thread workspace")
    args.workspace = checkpoint.workspace
    args.thread_id = checkpoint.thread_id
    args.resume_state = checkpoint_path
    args.human_input_root = human_input_root
    args.approval_root = approval_root
    run_result = build_single_harness(args).resume(checkpoint_path)
    run_dir = run_result.artifact_dir
    write_resume_link(
        run_dir,
        resumed_from_run_dir=Path(args.run_dir),
        resume_state=checkpoint_path,
        previous_run_id=checkpoint.run_id,
    )
    return run_dir
# endregion 1. Single-Agent continuation 结束


# region 2. 配置继承：防止 resume 静默改变原 Run 的执行契约
def _inherit_resolved_config(args: argparse.Namespace) -> None:
    """除非 resume 显式覆盖，否则继承源 run 的公开配置快照。"""

    config_path = Path(args.run_dir) / "resolved_config.json"
    if not config_path.exists():
        if Path(args.run_dir).exists():
            raise SystemExit(
                "cannot resume without the source run's resolved_config.json; "
                "pass an intact run directory to prevent configuration drift"
            )
        return
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid resume configuration: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != CONFIG_SCHEMA_VERSION
    ):
        raise SystemExit(
            "invalid resume configuration: unsupported resolved_config schema"
        )
    values = payload.get("values")
    if not isinstance(values, dict):
        raise SystemExit("invalid resume configuration: values must be an object")
    if (
        values.get("runtime_instructions_configured") is True
        and getattr(args, "runtime_instructions", None) is None
    ):
        raise SystemExit(
            "the source run used redacted runtime instructions; pass "
            "--runtime-instructions explicitly to resume without configuration drift"
        )
    for name, value in values.items():
        if name in _CONTINUATION_OWNED_CONFIG:
            continue
        if getattr(args, name, None) is None:
            setattr(args, name, value)
# endregion 2. 配置继承结束


def _reject_multi_agent_run(run_dir: str | Path) -> None:
    """把 Multi-Agent durable continuation 与 Single-Agent resume 分开。"""

    fanout_dir = Path(run_dir) / "fanout"
    if any(
        (fanout_dir / name).is_file()
        for name in (
            "fanout_plan.json",
            "fanout_checkpoint.json",
            "fanout_summary.json",
        )
    ):
        raise SystemExit(
            "forge resume cannot restore a Multi-Agent run; an interrupted "
            "HARD-only run must use forge run --agent-mode ultra "
            "--multi-agent-resume. A terminal Multi-Agent result requires a new run"
        )


# region 3. 人工决定：只回答 checkpoint 精确绑定的 pending item
def _persist_operator_decision(
    args: argparse.Namespace,
    *,
    checkpoint: TaskCheckpoint,
    human_input_root: str,
    approval_root: str,
) -> None:
    """只把人工决定写入当前 checkpoint 精确指向的 pending item。"""

    if checkpoint.status == "waiting_human":
        expected_request_id = str(
            checkpoint.metadata.get("human_input_request_id") or ""
        )
        if not expected_request_id:
            raise SystemExit(
                "waiting_human checkpoint is missing human_input_request_id"
            )
        requested_id = getattr(args, "request_id", "") or ""
        if requested_id and requested_id != expected_request_id:
            raise SystemExit(
                "human input request id does not match resume checkpoint: "
                f"expected={expected_request_id} actual={requested_id}"
            )
        pending_human_inputs = list_pending_human_inputs(human_input_root)
        selected_human_input = next(
            (
                item
                for item in pending_human_inputs
                if item.request_id == expected_request_id
            ),
            None,
        )
        # 对应请求可能已由其他入口回答；其他 Turn 的 pending item 不能阻止本次 resume，
        # 更不能被本 checkpoint 的 --answer 误写。
        if selected_human_input is None:
            return
        answer = getattr(args, "answer", None)
        if answer is None:
            raise SystemExit(
                "human input is pending; continue with "
                f"`forge resume {args.run_dir} --answer <answer>`"
            )
        respond_to_human_input(
            HumanInputResponseCommand(
                human_input_root=human_input_root,
                request_id=selected_human_input.request_id,
                answer=answer,
                note=getattr(args, "note", "") or "",
            )
        )
        return

    if checkpoint.status == "waiting_approval":
        pending_execution = checkpoint.pending_execution
        expected_operation_key = (
            pending_execution.pending_operation_key
            if pending_execution is not None
            else ""
        )
        if not expected_operation_key:
            raise SystemExit(
                "waiting_approval checkpoint is missing pending_operation_key"
            )
        requested_key = getattr(args, "operation_key", "") or ""
        if requested_key and requested_key != expected_operation_key:
            raise SystemExit(
                "approval operation key does not match resume checkpoint: "
                f"expected={expected_operation_key} actual={requested_key}"
            )
        pending_approvals = list_pending_approvals(approval_root)
        selected_approval = next(
            (
                item
                for item in pending_approvals
                if item.operation_key == expected_operation_key
            ),
            None,
        )
        # 已由其他入口决定时直接续跑；同目录下其他 Turn 的 pending approval 不属于
        # 本 checkpoint，不能在这里被选择。
        if selected_approval is None:
            return
        decision = getattr(args, "decision", None)
        if decision is None:
            raise SystemExit(
                "approval is pending; continue with "
                f"`forge resume {args.run_dir} --decision approved|rejected`"
            )
        decide_approval(
            approval_root,
            selected_approval.operation_key,
            decision,
            note=getattr(args, "note", "") or "",
        )
# endregion 3. 人工决定结束


def _control_root(value: str, checkpoint: TaskCheckpoint) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    workspace_path = Path(checkpoint.workspace) / path
    if workspace_path.exists() or not path.exists():
        return str(workspace_path.resolve())
    return str(path.resolve())


def write_resume_link(
    run_dir: str | Path,
    *,
    resumed_from_run_dir: str | Path,
    resume_state: str | Path,
    previous_run_id: str,
) -> tuple[Path, Path]:
    """写入机器可读和报告可见的 resume-chain artifacts。"""

    run_path = Path(run_dir)
    payload = {
        "resumed_from_run_dir": str(Path(resumed_from_run_dir)),
        "resume_state": str(Path(resume_state)),
        "previous_run_id": previous_run_id,
    }
    link_path = run_path / "resume_link.json"
    link_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    chain_path = run_path / "resume_chain.md"
    chain_text = "\n".join(
        [
            "# Resume Chain",
            "",
            f"- resumed_from_run_dir: `{payload['resumed_from_run_dir']}`",
            f"- resume_state: `{payload['resume_state']}`",
            f"- previous_run_id: `{payload['previous_run_id']}`",
            "",
        ]
    )
    chain_path.write_text(chain_text, encoding="utf-8")

    report_path = run_path / "usage_report.md"
    if report_path.exists():
        report = report_path.read_text(encoding="utf-8").rstrip()
        chain_body = "\n".join(chain_text.splitlines()[2:]) + "\n"
        report_path.write_text(
            f"{report}\n\n## Resume Chain\n\n{chain_body}",
            encoding="utf-8",
        )
    if (run_path / "run_manifest.json").exists():
        refresh_run_manifest(run_path)
    return link_path, chain_path


__all__ = [
    "resume_repository_task",
    "write_resume_link",
]

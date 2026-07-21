"""``forge resume``：从 durable state 构造新的显式 run。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable, Sequence, TypeVar

from agent_forge.cli.repository import run_repository_task
from agent_forge.configuration import CONFIG_SCHEMA_VERSION
from agent_forge.observability.api import refresh_run_manifest
from agent_forge.runtime.api import (
    HumanInputResponseCommand,
    decide_approval,
    latest_checkpoint_path,
    list_pending_approvals,
    list_pending_human_inputs,
    load_task_checkpoint,
    prepare_continuation,
    respond_to_human_input,
)
from agent_forge.runtime.application.operator_control import checkpoint_resume_workspace
from agent_forge.runtime.domain.task import TaskCheckpoint

_Pending = TypeVar("_Pending")
_CONTINUATION_OWNED_CONFIG = {
    "resume_state",
    "runtime_instructions_configured",
    "runtime_instructions_sha256",
    "task",
    "workspace",
}

# 主要入口：把 durable checkpoint 与人工决定装配成一个新的 continuation run。
def resume_repository_task(args: argparse.Namespace) -> Path:
    """加载 checkpoint/HITL 状态并启动新的 continuation run。"""

    checkpoint = load_task_checkpoint(latest_checkpoint_path(args.run_dir))
    _inherit_resolved_config(args)
    human_input_root = _control_root(
        args.human_input_root or ".agent_forge/human_input",
        checkpoint,
    )
    approval_root = _control_root(
        args.approval_root or ".agent_forge/approvals",
        checkpoint,
    )
    _persist_operator_decision(
        args,
        checkpoint_status=checkpoint.status,
        human_input_root=human_input_root,
        approval_root=approval_root,
    )
    try:
        checkpoint, checkpoint_path, plan = prepare_continuation(
            args.run_dir,
            human_input_root,
            override_task=args.task or "",
            workspace=args.workspace or "",
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    run_args = argparse.Namespace(
        task=plan.task,
        workspace=plan.workspace,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        temperature=args.temperature,
        max_steps=args.max_steps,
        max_context_chars=args.max_context_chars,
        max_prompt_tokens=args.max_prompt_tokens,
        reserved_output_tokens=args.reserved_output_tokens,
        model_context_window=args.model_context_window,
        native_tool_calling=args.native_tool_calling,
        parallel_tool_calls=args.parallel_tool_calls,
        structured_output=args.structured_output,
        reasoning_tokens=args.reasoning_tokens,
        prompt_cache=args.prompt_cache,
        supports_images=args.supports_images,
        approval_mode=args.approval_mode,
        auto_approve_writes=args.auto_approve_writes,
        approval_root=approval_root,
        human_input_root=human_input_root,
        human_thread_id=plan.human_thread_id,
        operation_ledger_root=args.operation_ledger_root,
        memory_root=args.memory_root,
        memory_recall_limit=args.memory_recall_limit,
        max_tool_calls_per_turn=args.max_tool_calls_per_turn,
        cost_budget_usd=args.cost_budget_usd,
        timeout_seconds=args.timeout_seconds,
        instruction_target=args.instruction_target,
        global_instruction_file=args.global_instruction_file,
        runtime_instructions=args.runtime_instructions,
        instruction_max_bytes=args.instruction_max_bytes,
        resume_state=checkpoint_path,
        output_root=args.output_root,
        agent_mode=args.agent_mode,
        profile=args.profile,
        max_revision_rounds=args.max_revision_rounds,
        skills=args.skills,
        skill_manifest=args.skill_manifest,
        mcp_config=args.mcp_config,
        mcp_tool=args.mcp_tool,
        enabled_tools=args.enabled_tools,
        execution_mode=args.execution_mode,
        network_policy=args.network_policy,
        keep_worktree=args.keep_worktree,
        tool_routing=args.tool_routing,
        container_runtime=args.container_runtime,
        container_image=args.container_image,
        container_cpus=args.container_cpus,
        container_memory=args.container_memory,
        container_pids_limit=args.container_pids_limit,
        container_read_only=args.container_read_only,
    )
    run_dir = run_repository_task(run_args)
    write_resume_link(
        run_dir,
        resumed_from_run_dir=Path(args.run_dir),
        resume_state=checkpoint_path,
        previous_run_id=checkpoint.run_id,
    )
    return run_dir


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
    source_agent_mode = values.get("agent_mode")
    if source_agent_mode == "fanout" and getattr(args, "agent_mode", None) is None:
        raise SystemExit(
            "forge resume cannot restore a fanout run; use forge run --fanout-resume"
        )
    for name, value in values.items():
        if name in _CONTINUATION_OWNED_CONFIG:
            continue
        if getattr(args, name, None) is None:
            setattr(args, name, value)


def _persist_operator_decision(
    args: argparse.Namespace,
    *,
    checkpoint_status: str,
    human_input_root: str,
    approval_root: str,
) -> None:
    """让 ``resume`` 同时承担待处理人工决定，避免记忆第二组入口。"""

    if checkpoint_status == "waiting_human":
        pending = list_pending_human_inputs(human_input_root)
        if not pending:
            return
        selected = _select_pending(
            pending,
            identity_name="request id",
            requested=getattr(args, "request_id", "") or "",
            identity=lambda item: item.request_id,
        )
        answer = getattr(args, "answer", None)
        if answer is None:
            raise SystemExit(
                "human input is pending; continue with "
                f"`forge resume {args.run_dir} --answer <answer>`"
            )
        respond_to_human_input(
            HumanInputResponseCommand(
                human_input_root=human_input_root,
                request_id=selected.request_id,
                answer=answer,
                note=getattr(args, "note", "") or "",
            )
        )
        return

    if checkpoint_status == "waiting_approval":
        pending = list_pending_approvals(approval_root)
        if not pending:
            return
        selected = _select_pending(
            pending,
            identity_name="operation key",
            requested=getattr(args, "operation_key", "") or "",
            identity=lambda item: item.operation_key,
        )
        decision = getattr(args, "decision", None)
        if decision is None:
            raise SystemExit(
                "approval is pending; continue with "
                f"`forge resume {args.run_dir} --decision approved|rejected`"
            )
        decide_approval(
            approval_root,
            selected.operation_key,
            decision,
            note=getattr(args, "note", "") or "",
        )


def _select_pending(
    items: Sequence[_Pending],
    *,
    identity_name: str,
    requested: str,
    identity: Callable[[_Pending], str],
) -> _Pending:
    if requested:
        for item in items:
            if identity(item) == requested:
                return item
        raise SystemExit(f"pending {identity_name} not found: {requested}")
    if len(items) != 1:
        values = ", ".join(identity(item) for item in items)
        raise SystemExit(
            f"multiple pending items; pass --{identity_name.replace(' ', '-')}: {values}"
        )
    return items[0]


def _control_root(value: str, checkpoint: TaskCheckpoint) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    workspace_path = Path(checkpoint_resume_workspace(checkpoint)) / path
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

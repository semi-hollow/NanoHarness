"""``forge resume``：从 durable state 构造新的显式 run。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_forge.cli.repository import run_repository_task
from agent_forge.runtime.api import prepare_continuation

# 主要入口：下方定义承接该模块的核心调用。
def resume_repository_task(args: argparse.Namespace) -> Path:
    """加载 checkpoint/HITL 状态并启动新的 continuation run。"""

    try:
        checkpoint, checkpoint_path, plan = prepare_continuation(
            args.run_dir,
            args.human_input_root,
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
        max_steps=args.max_steps,
        max_context_chars=args.max_context_chars,
        approval_mode=args.approval_mode,
        auto_approve_writes=args.auto_approve_writes,
        approval_root=args.approval_root,
        human_input_root=args.human_input_root,
        human_thread_id=plan.human_thread_id,
        operation_ledger_root=args.operation_ledger_root,
        resume_state=checkpoint_path,
        output_root=args.output_root,
        agent_mode=args.agent_mode,
        profile=args.profile,
        max_revision_rounds=args.max_revision_rounds,
        skills=args.skills,
        skill_manifest=args.skill_manifest,
        mcp_config=args.mcp_config,
        mcp_tool=args.mcp_tool,
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
    return link_path, chain_path

__all__ = [
    "resume_repository_task",
    "write_resume_link",
]

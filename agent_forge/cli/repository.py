"""``forge run`` 的跨 capability 装配与 artifact 发布。"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Callable

from agent_forge.multi_agent.api import (
    build_live_fanout,
    build_multi_agent_coordinator,
    load_fanout_plan,
)
from agent_forge.multi_agent.profiles import get_profile
from agent_forge.observability.api import TraceRecorder, write_usage_artifacts
from agent_forge.runtime.api import build_agent_loop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.execution_environment import (
    EnvironmentProbe,
    ExecutionEnvironment,
    ExecutionEnvironmentConfig,
)
from agent_forge.runtime.llm_config import LLMConfig, resolve_llm_config
from agent_forge.runtime.wiring import build_llm, build_registry
from agent_forge.tools.registry import ToolRegistry

# 主要入口：下方定义承接该模块的核心调用。
def run_repository_task(args: argparse.Namespace) -> Path:
    """执行 repository task，并返回完整 evidence 目录。"""

    run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:7]}"
    run_dir = Path(args.output_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "trace.json"
    trace = TraceRecorder(str(trace_path))
    environment, probe = prepare_execution_environment(args, run_id, run_dir)
    trace.add(
        0,
        "Runtime",
        "execution_environment",
        execution_environment=probe.to_dict(),
    )
    try:
        active_workspace = str(environment.active_workspace)
        llm_config = resolve_llm_config(
            provider=args.provider,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            timeout=60,
        )
        if not llm_config.is_configured():
            raise SystemExit(
                f"{args.provider} model config is incomplete. "
                "Set API env vars or pass --base-url/--api-key/--model."
            )
        config = _build_runtime_config(args, active_workspace, trace_path, environment)

        def registry_factory(
            workspace: str | Path,
            worker_environment: ExecutionEnvironment,
        ) -> ToolRegistry:
            return build_registry(
                str(workspace),
                auto=True,
                mcp_config_file=getattr(args, "mcp_config", None),
                mcp_allowed_tools=getattr(args, "mcp_tool", []),
                execution_environment=worker_environment,
            )

        agent_mode = getattr(args, "agent_mode", "single")
        if agent_mode == "fanout":
            final_answer = _run_fanout(
                args,
                config,
                trace,
                run_dir,
                llm_config,
                registry_factory,
            )
        else:
            registry = registry_factory(active_workspace, environment)
            llm = build_llm(llm_config)
            if agent_mode == "multi":
                profile = get_profile(getattr(args, "profile", "coding_fix"))
                summary = build_multi_agent_coordinator(
                    args.task,
                    profile,
                    config,
                    trace,
                    registry,
                    llm,
                    run_dir=run_dir,
                    max_revision_rounds=getattr(
                        args,
                        "max_revision_rounds",
                        profile.default_max_revision_rounds,
                    ),
                ).run()
                final_answer = summary.final_answer
            else:
                final_answer = build_agent_loop(config, trace, registry, llm).run(
                    args.task
                )

        trace.write()
        write_usage_artifacts(trace_path)
        (run_dir / "final_answer.txt").write_text(final_answer, encoding="utf-8")
        (run_dir / "patch.diff").write_text(environment.diff(), encoding="utf-8")
        _write_latest_run_pointer(run_dir)
        return run_dir
    finally:
        try:
            environment.write_manifest(run_dir)
        finally:
            environment.cleanup()

# 运行时端口：下方定义连接用例与外部实现。
def prepare_execution_environment(
    args: argparse.Namespace,
    run_id: str,
    run_dir: str | Path,
) -> tuple[ExecutionEnvironment, EnvironmentProbe]:
    """准备并记录 repository run 的执行边界。"""

    environment = ExecutionEnvironment(
        ExecutionEnvironmentConfig(
            mode=getattr(args, "execution_mode", "local"),
            workspace=args.workspace,
            run_id=run_id,
            network_policy=getattr(args, "network_policy", "deny"),
            keep_worktree=getattr(args, "keep_worktree", True),
            container_runtime=getattr(args, "container_runtime", "docker"),
            container_image=getattr(args, "container_image", "python:3.11-slim"),
            container_cpus=getattr(args, "container_cpus", 1.0),
            container_memory=getattr(args, "container_memory", "1g"),
            container_pids_limit=getattr(args, "container_pids_limit", 256),
            container_read_only=getattr(args, "container_read_only", True),
        )
    )
    probe = environment.prepare()
    environment.write_manifest(run_dir)
    return environment, probe


def _build_runtime_config(
    args: argparse.Namespace,
    active_workspace: str,
    trace_path: Path,
    environment: ExecutionEnvironment,
) -> RuntimeConfig:
    return RuntimeConfig(
        workspace=active_workspace,
        max_steps=args.max_steps,
        trace_file=str(trace_path),
        max_context_chars=args.max_context_chars,
        timeout_seconds=900,
        execution_environment=environment,
        task_state_root=str(trace_path.parent / "task_state"),
        resume_state=getattr(args, "resume_state", ""),
        auto_approve_writes=getattr(args, "auto_approve_writes", True),
        approval_root=getattr(args, "approval_root", ".agent_forge/approvals"),
        human_input_root=getattr(
            args,
            "human_input_root",
            ".agent_forge/human_input",
        ),
        human_thread_id=getattr(args, "human_thread_id", ""),
        operation_ledger_root=getattr(
            args,
            "operation_ledger_root",
            ".agent_forge/operation_ledger",
        ),
        approval_mode=args.approval_mode,
        skill_mode=parse_skill_mode(getattr(args, "skills", "auto")),
        skill_names=parse_skill_names(getattr(args, "skills", "auto")),
        skill_manifest_files=getattr(args, "skill_manifest", []),
        tool_routing_mode=getattr(args, "tool_routing", "task-aware"),
    )


def _run_fanout(
    args: argparse.Namespace,
    config: RuntimeConfig,
    trace: TraceRecorder,
    run_dir: Path,
    llm_config: LLMConfig,
    registry_factory: Callable[[Path, ExecutionEnvironment], ToolRegistry],
) -> str:
    plan_path = getattr(args, "fanout_plan", "")
    if not plan_path:
        raise SystemExit(
            "--fanout-plan is required when --agent-mode fanout is selected."
        )
    try:
        plan = load_fanout_plan(plan_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid fanout plan: {exc}") from exc
    trace.set_run_context(task=args.task)
    summary = build_live_fanout(
        plan=plan,
        base_config=config,
        trace=trace,
        run_dir=run_dir,
        llm_factory=lambda: build_llm(llm_config),
        registry_factory=registry_factory,
        max_workers=getattr(args, "max_workers", 4),
        resume_from=getattr(args, "fanout_resume", "") or None,
    ).run()
    final_answer = "\n".join(
        part
        for part in [
            summary.final_answer.strip(),
            f"fanout status: {summary.status}",
            f"report: {summary.report_path}",
            (
                "The integration patch is a candidate artifact; "
                "no official benchmark resolution is implied."
            ),
        ]
        if part
    )
    trace.set_run_context(
        stop_reason=f"fanout_{summary.status}",
        final_answer=final_answer,
    )
    return final_answer


def parse_skill_mode(value: str) -> str:
    return "none" if (value or "").strip().lower() == "none" else "auto"


def parse_skill_names(value: str) -> list[str]:
    normalized = (value or "").strip()
    if not normalized or normalized.lower() in {"auto", "none"}:
        return []
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _write_latest_run_pointer(run_dir: Path) -> None:
    latest = Path(".agent_forge/latest")
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "run.txt").write_text(str(run_dir), encoding="utf-8")

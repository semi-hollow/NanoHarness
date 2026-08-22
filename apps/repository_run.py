"""``forge run`` 的跨 capability 装配与 artifact 发布。"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Callable

from agent_forge._harness_support import control_path, write_latest_run_pointer
from apps.run_configuration import RunConfigDocument, resolved_run_config
from apps.run_composition import (
    build_single_harness,
    build_single_run_request,
    model_capabilities_from_args,
    parse_skill_mode,
    parse_skill_names,
    resolve_llm_config_from_args,
    resolve_repository_arguments,
)
from agent_forge.harness import HarnessExtensions, RunResult
from agent_forge.multi_agent.api import (
    AdaptivePlanner,
    FanoutPlan,
    LiveFanoutBuildRequest,
    PlanningOutcome,
    build_live_fanout,
    fanout_available_tools,
    load_fanout_plan,
    load_resume_initial_plan,
    resumed_planning_outcome,
    write_planning_artifact,
)
from agent_forge.observability.api import (
    TraceRecorder,
    write_run_manifest,
    write_usage_artifacts,
)
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.adapters.execution_environment import (
    EnvironmentProbe,
    ExecutionEnvironment,
    ExecutionEnvironmentConfig,
)
from agent_forge.runtime.adapters.model_config import LLMConfig
from agent_forge.runtime.wiring import (
    ToolRegistryBuildRequest,
    build_llm,
    build_registry,
)
from agent_forge.infrastructure.storage_layout import MEMORY_ROOT, ensure_storage_layout
from agent_forge.tools.registry import ToolRegistry


# 主要入口：从 CLI 参数装配 single、adaptive 或 manual fanout。
def run_repository_task(args: argparse.Namespace) -> Path:
    """把 CLI 输入转换为类型化请求；Single Agent 委托唯一 ``Harness`` API。"""

    config_document = resolve_repository_arguments(args)
    agent_mode = getattr(args, "agent_mode", "single") or "single"
    if agent_mode == "single":
        return execute_single_repository_task(args, config_document).artifact_dir
    if agent_mode == "adaptive":
        return _run_adaptive_repository_task(args, config_document)
    if agent_mode == "fanout":
        return _run_advanced_repository_task(args, config_document)
    raise SystemExit(f"unsupported agent mode: {agent_mode}")


def execute_single_repository_task(
    args: argparse.Namespace,
    config_document: RunConfigDocument | None,
    *,
    extensions: HarnessExtensions | None = None,
) -> RunResult:
    """规范主链：CLI 只选择 Adapter，再调用 ``Harness.run``。"""

    harness = build_single_harness(args, extensions=extensions)
    request = build_single_run_request(args, config_document)
    return harness.run(request)


def _run_advanced_repository_task(
    args: argparse.Namespace,
    config_document: RunConfigDocument | None,
    *,
    plan_override: FanoutPlan | None = None,
    planning_outcome: PlanningOutcome | None = None,
    replanner: AdaptivePlanner | None = None,
    allow_replan: bool = True,
) -> Path:
    """运行 manual/adaptive fanout；Single 仍只走 ``Harness.run``。"""

    requested_workspace = Path(args.workspace).expanduser().resolve()
    ensure_storage_layout(requested_workspace)
    run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:7]}"
    run_dir = Path(args.output_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "resolved_config.json").write_text(
        json.dumps(
            resolved_run_config(args, config_document),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    trace_path = run_dir / "trace.json"
    trace = TraceRecorder(str(trace_path))
    trace.set_run_context(task=args.task)
    environment, probe = prepare_execution_environment(args, run_id, run_dir)
    trace.add(
        0,
        "Runtime",
        "execution_environment",
        execution_environment=probe.to_dict(),
    )
    try:
        active_workspace = str(environment.active_workspace)
        llm_config = resolve_llm_config_from_args(args)
        config = _build_runtime_config(
            args,
            active_workspace,
            requested_workspace,
            trace_path,
            environment,
        )
        if planning_outcome is not None:
            write_planning_artifact(
                run_dir / "planning_decision.json",
                planning_outcome,
            )
            if planning_outcome.fallback_to_single:
                trace.add(
                    0,
                    "AdaptivePlanner",
                    "planning_fallback",
                    success=planning_outcome.decision is not None,
                    planning=planning_outcome.to_dict(),
                )
            else:
                trace.add(
                    0,
                    "AdaptivePlanner",
                    "planning_decision",
                    success=planning_outcome.decision is not None,
                    planning=planning_outcome.to_dict(),
                )

        def registry_factory(
            workspace: str | Path,
            worker_environment: ExecutionEnvironment,
        ) -> ToolRegistry:
            enabled_tools = getattr(args, "enabled_tools", None)
            return build_registry(
                ToolRegistryBuildRequest(
                    workspace=str(workspace),
                    auto=True,
                    mcp_config_file=getattr(args, "mcp_config", None),
                    mcp_allowed_tools=tuple(getattr(args, "mcp_tool", [])),
                    enabled_tools=(
                        tuple(enabled_tools) if enabled_tools is not None else None
                    ),
                    execution_environment=worker_environment,
                    memory_root=config.memory_root,
                    memory_namespace=config.memory_namespace,
                )
            )

        stop_output = _run_fanout(
            args,
            config,
            trace,
            run_dir,
            llm_config,
            registry_factory,
            plan_override=plan_override,
            replanner=replanner,
            allow_replan=allow_replan,
        )
        run_status = trace.stop_reason.removeprefix("fanout_") or "completed"
        trace.write()
        write_usage_artifacts(trace_path)
        (run_dir / "stop_output.txt").write_text(stop_output, encoding="utf-8")
        if run_status == "passed":
            (run_dir / "final_answer.txt").write_text(
                stop_output,
                encoding="utf-8",
            )
        (run_dir / "candidate_changes.diff").write_text(
            environment.diff(),
            encoding="utf-8",
        )
        write_run_manifest(
            run_dir,
            run_id=trace.run_id,
            task=args.task,
            status=run_status,
            stop_reason=trace.stop_reason,
        )
        # 证据导航必须在全部 run-local artifact 落盘后建立，否则分类视图会漏掉环境证据。
        environment.write_manifest(run_dir)
        _publish_advanced_run_pointer(requested_workspace, run_dir)
        return run_dir
    finally:
        try:
            # 异常路径仍尽量保存执行边界；成功路径已经在发布导航前完成落盘。
            if not (run_dir / "execution_environment.json").is_file():
                environment.write_manifest(run_dir)
        finally:
            environment.cleanup()


# 运行时端口：把 local/worktree/container 配置落成可执行 workspace 快照。
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
    requested_workspace: Path,
    trace_path: Path,
    environment: ExecutionEnvironment,
) -> RuntimeConfig:
    return RuntimeConfig(
        workspace=active_workspace,
        max_steps=args.max_steps,
        trace_file=str(trace_path),
        max_context_chars=args.max_context_chars,
        max_prompt_tokens=getattr(args, "max_prompt_tokens", 32_768),
        reserved_output_tokens=getattr(args, "reserved_output_tokens", 4_096),
        timeout_seconds=getattr(args, "timeout_seconds", 900.0),
        cost_budget_usd=getattr(args, "cost_budget_usd", None),
        execution_environment=environment,
        task_state_root=str(trace_path.parent / "task_state"),
        resume_state=getattr(args, "resume_state", ""),
        auto_approve_writes=getattr(args, "auto_approve_writes", True),
        approval_root=str(
            control_path(
                getattr(args, "approval_root", ""),
                requested_workspace,
                "approvals",
            )
        ),
        human_input_root=str(
            control_path(
                getattr(args, "human_input_root", ""),
                requested_workspace,
                "human_input",
            )
        ),
        human_thread_id=getattr(args, "human_thread_id", ""),
        operation_ledger_root=str(
            control_path(
                getattr(args, "operation_ledger_root", ""),
                requested_workspace,
                "operation_ledger",
            )
        ),
        approval_mode=args.approval_mode,
        skill_mode=parse_skill_mode(getattr(args, "skills", "auto")),
        skill_names=parse_skill_names(getattr(args, "skills", "auto")),
        skill_manifest_files=getattr(args, "skill_manifest", []),
        tool_routing_mode=getattr(args, "tool_routing", "task-aware"),
        memory_root=str(
            Path(getattr(args, "memory_root", "") or MEMORY_ROOT).expanduser()
        ),
        memory_namespace=str(requested_workspace),
        memory_max_chars=getattr(args, "memory_max_chars", 2_000),
        max_tool_calls_per_turn=getattr(args, "max_tool_calls_per_turn", 4),
        model_capabilities=model_capabilities_from_args(args),
        instruction_target=getattr(args, "instruction_target", ""),
        global_instruction_files=getattr(args, "global_instruction_file", []),
        runtime_instructions=getattr(args, "runtime_instructions", ""),
        instruction_max_bytes=getattr(args, "instruction_max_bytes", 2_600),
    )


def _run_fanout(
    args: argparse.Namespace,
    config: RuntimeConfig,
    trace: TraceRecorder,
    run_dir: Path,
    llm_config: LLMConfig,
    registry_factory: Callable[[Path, ExecutionEnvironment], ToolRegistry],
    *,
    plan_override: FanoutPlan | None = None,
    replanner: AdaptivePlanner | None = None,
    allow_replan: bool = True,
) -> str:
    plan = plan_override
    if plan is None:
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
        LiveFanoutBuildRequest(
            plan=plan,
            base_config=config,
            trace=trace,
            run_dir=run_dir,
            llm_factory=lambda: build_llm(llm_config),
            registry_factory=registry_factory,
            max_workers=getattr(args, "max_workers", 4),
            resume_from=getattr(args, "fanout_resume", "") or None,
            replanner=replanner,
            allow_replan=allow_replan,
        )
    ).run()
    stop_output = "\n".join(
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
        stop_output=stop_output,
        final_answer=stop_output if summary.status == "passed" else None,
    )
    return stop_output


def _run_adaptive_repository_task(
    args: argparse.Namespace,
    config_document: RunConfigDocument | None,
) -> Path:
    """Planner 选择 route；Single route 仍调用现有 Harness。"""

    if getattr(args, "fanout_plan", None):
        raise SystemExit("--fanout-plan belongs to --agent-mode fanout, not adaptive.")
    llm_config = resolve_llm_config_from_args(args)
    resume_from = getattr(args, "fanout_resume", "") or ""
    if resume_from:
        try:
            plan = load_resume_initial_plan(resume_from)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise SystemExit(f"invalid adaptive resume: {exc}") from exc
        outcome = resumed_planning_outcome(plan)
        return _run_advanced_repository_task(
            args,
            config_document,
            plan_override=plan,
            planning_outcome=outcome,
            replanner=None,
            allow_replan=False,
        )

    planner = AdaptivePlanner(
        model_factory=lambda: build_llm(llm_config),
        available_tools=_available_planner_tools(args),
        max_fanout_tasks=16,
        max_steps=args.max_steps,
    )
    outcome = planner.decide(args.task, args.workspace)
    if outcome.decision is None or outcome.fallback_to_single:
        result = execute_single_repository_task(args, config_document)
        write_planning_artifact(
            result.artifact_dir / "planning_decision.json",
            outcome,
        )
        return result.artifact_dir
    if outcome.decision.mode == "single":
        result = execute_single_repository_task(args, config_document)
        write_planning_artifact(
            result.artifact_dir / "planning_decision.json",
            outcome,
        )
        return result.artifact_dir
    plan = outcome.decision.to_fanout_plan(args.task)
    return _run_advanced_repository_task(
        args,
        config_document,
        plan_override=plan,
        planning_outcome=outcome,
        replanner=planner,
    )


def _available_planner_tools(args: argparse.Namespace) -> list[str]:
    configured = getattr(args, "enabled_tools", None)
    if configured is not None:
        return sorted(set(configured) | set(getattr(args, "mcp_tool", [])))
    return sorted(set(fanout_available_tools()) | set(getattr(args, "mcp_tool", [])))


def _publish_advanced_run_pointer(workspace: Path, run_dir: Path) -> None:
    """让 Multi/Fanout 与 Single Agent 使用同一原生 run 发现指针。"""

    write_latest_run_pointer(workspace, run_dir)

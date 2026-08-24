"""``forge run`` 的产品入口与 repository execution 装配。

系统角色：把用户的 Single / Ultra 策略选择转成唯一 Single-Agent
``Harness.run`` 或已校验 Multi-Agent Plan。本文件只做产品路由和运行装配，
不实现 AgentLoop、Planner 校验或 Worker 调度。

折叠导航：1 Single / Ultra 公开入口；2 Multi-Agent Run 与 artifacts；
3 Runtime 依赖装配；4 validated plan 执行与发布。
"""

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


# region 1. 公开策略入口：Single 直达 AgentLoop，Ultra 强制先规划
def run_repository_task(args: argparse.Namespace) -> Path:
    """把 CLI 输入转成 Single 或 Ultra；不暴露第三种产品模式。"""

    config_document = resolve_repository_arguments(args)
    agent_mode = getattr(args, "agent_mode", "single") or "single"
    if agent_mode == "single":
        return execute_single_repository_task(args, config_document).artifact_dir
    if agent_mode == "ultra":
        return _run_ultra_repository_task(args, config_document)
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
# endregion 1. 公开策略入口结束


# region 2. Multi-Agent Run：只接收 Planner/Resume 已经确定的 canonical plan
def _run_multi_agent_plan(
    args: argparse.Namespace,
    config_document: RunConfigDocument | None,
    *,
    plan: FanoutPlan,
    planning_outcome: PlanningOutcome | None = None,
    replanner: AdaptivePlanner | None = None,
    allow_replan: bool = True,
    resume_from: str | None = None,
) -> Path:
    """为已校验 Multi-Agent Plan 创建独立 Run，再发布可审计产物。"""

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
            # 进入本函数的 outcome 已确定为 Multi；Single/fallback 不创建 Coordinator。
            trace.add(
                0,
                "AdaptivePlanner",
                "planning_decision",
                success=True,
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

        stop_output = _execute_validated_plan(
            args,
            config,
            trace,
            run_dir,
            llm_config,
            registry_factory,
            plan=plan,
            replanner=replanner,
            allow_replan=allow_replan,
            resume_from=resume_from,
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
        _publish_multi_agent_run_pointer(requested_workspace, run_dir)
        return run_dir
    finally:
        try:
            # 异常路径仍尽量保存执行边界；成功路径已经在发布导航前完成落盘。
            if not (run_dir / "execution_environment.json").is_file():
                environment.write_manifest(run_dir)
        finally:
            environment.cleanup()
# endregion 2. Multi-Agent Run 结束


# region 3. Runtime 装配：固定 workspace 边界和每个 Worker 共用的 RuntimeConfig
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
        requested_workspace=str(requested_workspace),
        execution_mode=environment.probe().mode,
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
# endregion 3. Runtime 装配结束


# region 4. Validated Plan 执行：Coordinator 拥有调度，本层只投影终态
def _execute_validated_plan(
    args: argparse.Namespace,
    config: RuntimeConfig,
    trace: TraceRecorder,
    run_dir: Path,
    llm_config: LLMConfig,
    registry_factory: Callable[[Path, ExecutionEnvironment], ToolRegistry],
    *,
    plan: FanoutPlan,
    replanner: AdaptivePlanner | None = None,
    allow_replan: bool = True,
    resume_from: str | None = None,
) -> str:
    """执行 Runtime 已接受的 plan；正常用户无法从文件绕过 Planner。"""

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
            resume_from=resume_from,
            replanner=replanner,
            allow_replan=allow_replan,
        )
    ).run()
    stop_output = "\n".join(
        part
        for part in [
            summary.final_answer.strip(),
            f"Multi-Agent status: {summary.status}",
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


def _run_ultra_repository_task(
    args: argparse.Namespace,
    config_document: RunConfigDocument | None,
) -> Path:
    """Ultra 先经 Planner；Single 决策始终回到同一 ``Harness.run``。"""

    llm_config = resolve_llm_config_from_args(args)
    resume_from = getattr(args, "multi_agent_resume", "") or ""
    if resume_from:
        try:
            plan = load_resume_initial_plan(resume_from)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise SystemExit(f"invalid Multi-Agent resume: {exc}") from exc
        outcome = resumed_planning_outcome(plan)
        return _run_multi_agent_plan(
            args,
            config_document,
            plan=plan,
            planning_outcome=outcome,
            replanner=None,
            allow_replan=False,
            resume_from=resume_from,
        )

    planner = AdaptivePlanner(
        model_factory=lambda: build_llm(llm_config),
        available_tools=_available_planner_tools(args),
        max_fanout_tasks=16,
        max_steps=args.max_steps,
    )
    outcome = planner.decide(args.task, args.workspace)
    # 无合法计划或显式 Single 都回到同一个 canonical Harness.run；不复制第二套 Runtime。
    if outcome.decision is None or outcome.decision.mode == "single":
        result = execute_single_repository_task(args, config_document)
        write_planning_artifact(
            result.artifact_dir / "planning_decision.json",
            outcome,
        )
        return result.artifact_dir
    plan = outcome.decision.to_fanout_plan(args.task)
    return _run_multi_agent_plan(
        args,
        config_document,
        plan=plan,
        planning_outcome=outcome,
        replanner=planner,
    )


def _available_planner_tools(args: argparse.Namespace) -> list[str]:
    configured = getattr(args, "enabled_tools", None)
    if configured is not None:
        return sorted(set(configured) | set(getattr(args, "mcp_tool", [])))
    return sorted(set(fanout_available_tools()) | set(getattr(args, "mcp_tool", [])))


def _publish_multi_agent_run_pointer(workspace: Path, run_dir: Path) -> None:
    """让 Multi/Fanout 与 Single Agent 使用同一原生 run 发现指针。"""

    write_latest_run_pointer(workspace, run_dir)
# endregion 4. Validated Plan 执行结束

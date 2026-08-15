"""``forge run`` 的跨 capability 装配与 artifact 发布。"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Callable

from agent_forge._harness_support import control_path, write_latest_run_pointer
from agent_forge.configuration import (
    RunConfigDocument,
    resolve_run_arguments,
    resolved_run_config,
)
from agent_forge.harness import (
    Harness,
    HarnessConfig,
    HarnessExtensions,
    RunRequest,
    RunResult,
)
from agent_forge.multi_agent.api import (
    LiveFanoutBuildRequest,
    SequentialCoordinatorBuildRequest,
    build_live_fanout,
    build_multi_agent_coordinator,
    load_fanout_plan,
)
from agent_forge.multi_agent.profiles import get_profile
from agent_forge.observability.api import (
    TraceRecorder,
    write_run_manifest,
    write_usage_artifacts,
)
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.execution_environment import (
    EnvironmentProbe,
    ExecutionEnvironment,
    ExecutionEnvironmentConfig,
)
from agent_forge.runtime.llm_config import (
    LLMConfig,
    LLMConfigRequest,
    default_model_capabilities,
    resolve_llm_config,
)
from agent_forge.runtime.wiring import (
    ToolRegistryBuildRequest,
    build_llm,
    build_registry,
)
from agent_forge.storage_layout import MEMORY_ROOT, ensure_storage_layout
from agent_forge.tools.registry import ToolRegistry


# 主要入口：从 CLI 参数装配并运行 single、sequential multi 或 live fanout 任务。
def run_repository_task(args: argparse.Namespace) -> Path:
    """把 CLI 输入转换为类型化请求；Single Agent 委托唯一 ``Harness`` API。"""

    config_document = resolve_repository_arguments(args)
    if getattr(args, "agent_mode", "single") == "single":
        return execute_single_repository_task(args, config_document).artifact_dir
    return _run_advanced_repository_task(args, config_document)


def resolve_repository_arguments(
    args: argparse.Namespace,
) -> RunConfigDocument | None:
    """合并 CLI、环境变量、配置文件和默认值，供 CLI/TUI 共享。"""

    try:
        return resolve_run_arguments(args)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid run configuration: {exc}") from exc


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


def build_single_harness(
    args: argparse.Namespace,
    *,
    extensions: HarnessExtensions | None = None,
) -> Harness:
    """装配 single-agent Harness；Operator Console 复用这里而不复制 Runtime。"""

    enabled_tools = getattr(args, "enabled_tools", None)
    return Harness(
        model=build_llm(_resolve_llm_config(args)),
        config=HarnessConfig(
            workspace=args.workspace,
            output_root=args.output_root,
            max_steps=args.max_steps,
            max_context_chars=args.max_context_chars,
            max_prompt_tokens=args.max_prompt_tokens,
            reserved_output_tokens=args.reserved_output_tokens,
            max_tool_calls_per_turn=args.max_tool_calls_per_turn,
            timeout_seconds=args.timeout_seconds,
            cost_budget_usd=args.cost_budget_usd,
            approval_mode=args.approval_mode,
            auto_approve_writes=args.auto_approve_writes,
            approval_root=args.approval_root,
            human_input_root=args.human_input_root,
            operation_ledger_root=args.operation_ledger_root,
            memory_root=args.memory_root,
            memory_max_chars=args.memory_max_chars,
            skill_mode=parse_skill_mode(args.skills),
            skill_names=tuple(parse_skill_names(args.skills)),
            skill_manifest_files=tuple(args.skill_manifest),
            tool_routing_mode=args.tool_routing,
            enabled_tools=(tuple(enabled_tools) if enabled_tools is not None else None),
            mcp_config_file=args.mcp_config,
            mcp_allowed_tools=tuple(args.mcp_tool),
            model_capabilities=_model_capabilities_from_args(args),
            instruction_target=args.instruction_target,
            global_instruction_files=tuple(args.global_instruction_file),
            runtime_instructions=args.runtime_instructions,
            instruction_max_bytes=args.instruction_max_bytes,
            execution_mode=args.execution_mode,
            network_policy=args.network_policy,
            keep_worktree=args.keep_worktree,
            container_runtime=args.container_runtime,
            container_image=args.container_image,
            container_cpus=args.container_cpus,
            container_memory=args.container_memory,
            container_pids_limit=args.container_pids_limit,
            container_read_only=args.container_read_only,
        ),
        extensions=extensions,
    )


def build_single_run_request(
    args: argparse.Namespace,
    config_document: RunConfigDocument | None,
) -> RunRequest:
    """把已解析参数收敛为 Public API 输入。"""

    return RunRequest(
        task=args.task,
        resume_state=getattr(args, "resume_state", "") or "",
        human_thread_id=getattr(args, "human_thread_id", "") or "",
        resolved_config=resolved_run_config(args, config_document),
    )


def _run_advanced_repository_task(
    args: argparse.Namespace,
    config_document: RunConfigDocument | None,
) -> Path:
    """保留 Multi/Fanout 高级编排；它们不属于 Single-Agent 黄金主链。"""

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
        llm_config = _resolve_llm_config(args)
        config = _build_runtime_config(
            args,
            active_workspace,
            requested_workspace,
            trace_path,
            environment,
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

        agent_mode = getattr(args, "agent_mode", "single")
        if agent_mode == "fanout":
            stop_output = _run_fanout(
                args,
                config,
                trace,
                run_dir,
                llm_config,
                registry_factory,
            )
            run_status = trace.stop_reason.removeprefix("fanout_") or "completed"
        else:
            registry = registry_factory(active_workspace, environment)
            llm = build_llm(llm_config)
            profile = get_profile(getattr(args, "profile", "coding_fix"))
            summary = build_multi_agent_coordinator(
                SequentialCoordinatorBuildRequest(
                    task=args.task,
                    profile=profile,
                    runtime_config=config,
                    trace=trace,
                    registry=registry,
                    llm=llm,
                    run_dir=run_dir,
                    max_revision_rounds=getattr(
                        args,
                        "max_revision_rounds",
                        profile.default_max_revision_rounds,
                    ),
                )
            ).run()
            stop_output = summary.final_answer
            run_status = summary.status
            trace.set_run_context(
                stop_reason=f"multi_agent_{summary.status}",
                stop_output=stop_output,
                final_answer=stop_output if summary.status == "passed" else None,
            )

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


def _resolve_llm_config(args: argparse.Namespace) -> LLMConfig:
    """在创建 run 前解析模型 Adapter，并拒绝不完整凭据。"""

    config = resolve_llm_config(
        LLMConfigRequest(
            provider=args.provider,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            timeout=60,
            temperature=args.temperature,
            thinking_mode=args.thinking_mode,
            reasoning_effort=args.reasoning_effort,
            capabilities=_model_capabilities_from_args(args),
        )
    )
    if not config.is_configured():
        raise SystemExit(
            f"{args.provider} model config is incomplete. "
            "Set API env vars or pass --base-url/--api-key/--model."
        )
    return config


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
        model_capabilities=_model_capabilities_from_args(args),
        instruction_target=getattr(args, "instruction_target", ""),
        global_instruction_files=getattr(args, "global_instruction_file", []),
        runtime_instructions=getattr(args, "runtime_instructions", ""),
        instruction_max_bytes=getattr(args, "instruction_max_bytes", 2_600),
    )


def _model_capabilities_from_args(args: argparse.Namespace) -> ModelCapabilities:
    """将 CLI/config 的模型声明转换为 Runtime 唯一能力对象。"""

    provider = str(args.provider or "")
    model = str(
        args.model or ("deepseek-v4-flash" if provider == "deepseek" else "")
    )
    inferred = default_model_capabilities(
        provider=provider,
        model=model,
        thinking_mode=str(args.thinking_mode or "auto"),
    )
    configured_context_window = int(args.model_context_window or 0)
    return ModelCapabilities(
        native_tool_calling=bool(args.native_tool_calling),
        parallel_tool_calls=bool(args.parallel_tool_calls),
        structured_output=bool(args.structured_output),
        reasoning_tokens=bool(args.reasoning_tokens or args.thinking_mode == "enabled"),
        prompt_cache=bool(args.prompt_cache),
        context_window=configured_context_window or inferred.context_window,
        supports_images=bool(args.supports_images),
        source=("resolved_run_config" if configured_context_window else inferred.source),
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
        LiveFanoutBuildRequest(
            plan=plan,
            base_config=config,
            trace=trace,
            run_dir=run_dir,
            llm_factory=lambda: build_llm(llm_config),
            registry_factory=registry_factory,
            max_workers=getattr(args, "max_workers", 4),
            resume_from=getattr(args, "fanout_resume", "") or None,
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


def parse_skill_mode(value: str) -> str:
    return "none" if (value or "").strip().lower() == "none" else "auto"


def parse_skill_names(value: str) -> list[str]:
    normalized = (value or "").strip()
    if not normalized or normalized.lower() in {"auto", "none"}:
        return []
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _publish_advanced_run_pointer(workspace: Path, run_dir: Path) -> None:
    """让 Multi/Fanout 与 Single Agent 使用同一原生 run 发现指针。"""

    write_latest_run_pointer(workspace, run_dir)

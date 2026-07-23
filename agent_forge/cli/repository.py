"""``forge run`` 的跨 capability 装配与 artifact 发布。"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Callable

from agent_forge.configuration import (
    RunConfigDocument,
    resolve_run_arguments,
    resolved_run_config,
)
from agent_forge.harness import Harness, HarnessConfig, RunRequest
from agent_forge.multi_agent.api import (
    LiveFanoutBuildRequest,
    SequentialCoordinatorBuildRequest,
    build_live_fanout,
    build_multi_agent_coordinator,
    load_fanout_plan,
)
from agent_forge.multi_agent.profiles import get_profile
from agent_forge.observability.api import TraceRecorder, write_usage_artifacts
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
    resolve_llm_config,
)
from agent_forge.runtime.wiring import (
    ToolRegistryBuildRequest,
    build_llm,
    build_registry,
)
from agent_forge.tools.registry import ToolRegistry


# 主要入口：从 CLI 参数装配并运行 single、sequential multi 或 live fanout 任务。
def run_repository_task(args: argparse.Namespace) -> Path:
    """把 CLI 输入转换为类型化请求；Single Agent 委托唯一 ``Harness`` API。"""

    try:
        config_document = resolve_run_arguments(args)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid run configuration: {exc}") from exc
    if getattr(args, "agent_mode", "single") == "single":
        return _run_single_repository_task(args, config_document)
    return _run_advanced_repository_task(args, config_document)


def _run_single_repository_task(
    args: argparse.Namespace,
    config_document: RunConfigDocument | None,
) -> Path:
    """规范主链：CLI 只选择 Adapter，再调用 ``Harness.run``。"""

    llm = build_llm(_resolve_llm_config(args))
    enabled_tools = getattr(args, "enabled_tools", None)
    harness = Harness(
        model=llm,
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
            memory_recall_limit=args.memory_recall_limit,
            skill_mode=parse_skill_mode(args.skills),
            skill_names=tuple(parse_skill_names(args.skills)),
            skill_manifest_files=tuple(args.skill_manifest),
            tool_routing_mode=args.tool_routing,
            enabled_tools=(
                tuple(enabled_tools) if enabled_tools is not None else None
            ),
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
    )
    result = harness.run(
        RunRequest(
            task=args.task,
            resume_state=getattr(args, "resume_state", "") or "",
            human_thread_id=getattr(args, "human_thread_id", "") or "",
            resolved_config=resolved_run_config(args, config_document),
        )
    )
    return result.artifact_dir


def _run_advanced_repository_task(
    args: argparse.Namespace,
    config_document: RunConfigDocument | None,
) -> Path:
    """保留 Multi/Fanout 高级编排；它们不属于 Single-Agent 黄金主链。"""

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
        config = _build_runtime_config(args, active_workspace, trace_path, environment)

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
                )
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
            final_answer = summary.final_answer

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
        memory_root=getattr(args, "memory_root", ".agent_forge/memory"),
        memory_namespace=str(Path(getattr(args, "workspace", ".")).resolve()),
        memory_recall_limit=getattr(args, "memory_recall_limit", 6),
        max_tool_calls_per_turn=getattr(args, "max_tool_calls_per_turn", 4),
        model_capabilities=_model_capabilities_from_args(args),
        instruction_target=getattr(args, "instruction_target", ""),
        global_instruction_files=getattr(args, "global_instruction_file", []),
        runtime_instructions=getattr(args, "runtime_instructions", ""),
        instruction_max_bytes=getattr(args, "instruction_max_bytes", 2_600),
    )


def _model_capabilities_from_args(args: argparse.Namespace) -> ModelCapabilities:
    """将 CLI/config 的模型声明转换为 Runtime 唯一能力对象。"""

    return ModelCapabilities(
        native_tool_calling=bool(args.native_tool_calling),
        parallel_tool_calls=bool(args.parallel_tool_calls),
        structured_output=bool(args.structured_output),
        reasoning_tokens=bool(
            args.reasoning_tokens or args.thinking_mode == "enabled"
        ),
        prompt_cache=bool(args.prompt_cache),
        context_window=int(args.model_context_window),
        supports_images=bool(args.supports_images),
        source="resolved_run_config",
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

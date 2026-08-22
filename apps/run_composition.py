"""Single-Agent 入站应用共用的 Harness 请求与依赖装配。"""

from __future__ import annotations

import argparse

from apps.run_configuration import (
    RunConfigDocument,
    resolve_run_arguments,
    resolved_run_config,
)
from agent_forge.harness import (
    Harness,
    HarnessConfig,
    HarnessExtensions,
    RunRequest,
)
from agent_forge.runtime.adapters.model_config import (
    LLMConfig,
    LLMConfigRequest,
    default_model_capabilities,
    resolve_llm_config,
)
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.wiring import build_llm


def resolve_repository_arguments(
    args: argparse.Namespace,
) -> RunConfigDocument | None:
    """合并 CLI、环境变量、配置文件和默认值，供入站应用共享。"""

    try:
        return resolve_run_arguments(args)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"invalid run configuration: {exc}") from exc


def build_single_harness(
    args: argparse.Namespace,
    *,
    extensions: HarnessExtensions | None = None,
) -> Harness:
    """将入站配置装配为 standalone Single-Agent 的公开 Harness。"""

    enabled_tools = getattr(args, "enabled_tools", None)
    return Harness(
        model=build_llm(resolve_llm_config_from_args(args)),
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
            model_capabilities=model_capabilities_from_args(args),
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
    """把已解析的入站参数收敛为 Harness Public API 输入。"""

    return RunRequest(
        task=args.task,
        resume_state=getattr(args, "resume_state", "") or "",
        human_thread_id=getattr(args, "human_thread_id", "") or "",
        resolved_config=resolved_run_config(args, config_document),
        run_label=getattr(args, "run_label", "") or "",
    )


def resolve_llm_config_from_args(args: argparse.Namespace) -> LLMConfig:
    """在创建 Run 前解析 Model Adapter，并拒绝不完整凭据。"""

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
            capabilities=model_capabilities_from_args(args),
        )
    )
    if not config.is_configured():
        raise SystemExit(
            f"{args.provider} model config is incomplete. "
            "Set API env vars or pass --base-url/--api-key/--model."
        )
    return config


def model_capabilities_from_args(args: argparse.Namespace) -> ModelCapabilities:
    """将入站配置的模型声明转换为 Runtime 唯一能力对象。"""

    provider = str(args.provider or "")
    model = str(args.model or ("deepseek-v4-flash" if provider == "deepseek" else ""))
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
        source=(
            "resolved_run_config" if configured_context_window else inferred.source
        ),
    )


def parse_skill_mode(value: str) -> str:
    return "none" if (value or "").strip().lower() == "none" else "auto"


def parse_skill_names(value: str) -> list[str]:
    normalized = (value or "").strip()
    if not normalized or normalized.lower() in {"auto", "none"}:
        return []
    return [item.strip() for item in normalized.split(",") if item.strip()]

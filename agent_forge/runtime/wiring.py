"""所有入站入口共用的 Runtime 依赖装配点。

本模块是 Runtime 中唯一同时认识 Application 和具体 Adapter 的位置。
"""

from __future__ import annotations

from agent_forge.models.gateway import ModelGateway, RetryPolicy
from agent_forge.observability.adapters.json_trace import TraceRecorder
from agent_forge.runtime.adapters import (
    JsonApprovalRepository,
    JsonHumanInputRepository,
    JsonOperationLedgerRepository,
    JsonTaskStateRepository,
    RepositoryContextAssembler,
)
from agent_forge.runtime.application.agent_loop import AgentLoop
from agent_forge.runtime.application.dependencies import RuntimeDependencies
from agent_forge.runtime.application.operator_control import (
    BuildContinuationPlan,
    ContinuationPlan,
    DecideApproval,
    RespondToHumanInput,
)
from agent_forge.runtime.domain.approval import ApprovalRequest
from agent_forge.runtime.domain.human_input import HumanInputRequest
from agent_forge.runtime.domain.task import TaskCheckpoint
from agent_forge.runtime.execution_environment import ExecutionEnvironment
from agent_forge.runtime.execution_environment import ExecutionEnvironmentConfig
from agent_forge.runtime.hooks import HookManager
from agent_forge.runtime.llm_client import OpenAICompatibleLLMClient
from agent_forge.runtime.llm_client import LLMClient
from agent_forge.runtime.llm_config import LLMConfig
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.skills import build_default_skill_registry
from agent_forge.tools.apply_patch import ApplyPatchTool
from agent_forge.tools.ask_human import AskHumanTool
from agent_forge.tools.diagnostics import DiagnosticsTool
from agent_forge.tools.git_diff import GitDiffTool
from agent_forge.tools.git_status import GitStatusTool
from agent_forge.tools.grep import GrepSearchTool, GrepTool
from agent_forge.tools.list_files import ListFilesTool
from agent_forge.tools.mcp_config import MCPConfigLoader
from agent_forge.tools.read_file import ReadFileTool
from agent_forge.tools.registry import ToolRegistry
from agent_forge.tools.run_command import RunCommandTool
from agent_forge.tools.write_file import WriteFileTool


def build_registry(
    workspace: str,
    auto: bool,
    mcp_config_file: str | None = None,
    mcp_allowed_tools: list[str] | None = None,
    execution_environment: ExecutionEnvironment | None = None,
) -> ToolRegistry:
    """构造 AgentLoop 使用的受治理工具注册表。"""

    sandbox = WorkspaceSandbox(workspace)
    registry = ToolRegistry()
    for tool in [
        ListFilesTool(sandbox),
        ReadFileTool(sandbox),
        WriteFileTool(sandbox, auto),
        GrepTool(sandbox),
        GrepSearchTool(sandbox),
        ApplyPatchTool(sandbox, auto),
        RunCommandTool(sandbox, auto, execution_environment=execution_environment),
        GitStatusTool(sandbox),
        GitDiffTool(sandbox),
        DiagnosticsTool(sandbox, execution_environment=execution_environment),
        AskHumanTool(),
    ]:
        registry.register(tool)
    if mcp_config_file:
        registry.mcp_config_report = MCPConfigLoader(sandbox).load_into(
            registry,
            mcp_config_file,
            allowed_tools=mcp_allowed_tools,
        )
    return registry


def build_llm(config: LLMConfig) -> ModelGateway:
    """根据已解析配置构造统一模型网关。"""

    if config.uses_openai_compatible_api:
        return ModelGateway(
            OpenAICompatibleLLMClient.from_config(config),
            provider=config.provider,
            model=config.model or "unknown",
            retry_policy=RetryPolicy(max_attempts=2),
        )
    raise ValueError(f"Unsupported LLM provider: {config.provider}")


def build_runtime_dependencies(
    config: "RuntimeConfig",
    trace: TraceRecorder,
    registry: ToolRegistry,
    llm: LLMClient | None,
) -> RuntimeDependencies:
    """一次性装配 AgentLoop 需要的全部出站端口实现。

    CLI、benchmark 和 multi-agent 都必须通过这里装配 Runtime，避免不同入口偷偷
    创建不同的审批、恢复或幂等行为。
    """

    if llm is None:
        raise ValueError(
            "AgentLoop requires a real LLM client; build it through runtime.wiring"
        )
    environment = config.execution_environment or ExecutionEnvironment(
        ExecutionEnvironmentConfig(workspace=config.workspace)
    )
    hooks = HookManager.default(
        environment,
        config.auto_approve_writes,
        approval_mode=config.approval_mode,
    )
    return RuntimeDependencies(
        events=trace,
        context=RepositoryContextAssembler(),
        skills=build_default_skill_registry(
            getattr(config, "skill_manifest_files", [])
        ),
        tools=registry,
        model=llm,
        environment=environment,
        hooks=hooks,
        task_states=JsonTaskStateRepository(config.task_state_root),
        approvals=JsonApprovalRepository(config.approval_root),
        human_inputs=JsonHumanInputRepository(config.human_input_root),
        operations=JsonOperationLedgerRepository(config.operation_ledger_root),
    )

# 主要入口：下方定义承接该模块的核心调用。
def build_agent_loop(
    config: "RuntimeConfig",
    trace: TraceRecorder,
    registry: ToolRegistry,
    llm: LLMClient | None,
) -> AgentLoop:
    """返回已经注入全部端口实现的标准单 Agent 用例。"""

    return AgentLoop(config, build_runtime_dependencies(config, trace, registry, llm))


def decide_approval(
    approval_root: str,
    operation_key: str,
    decision: str,
    *,
    note: str = "",
) -> ApprovalRequest:
    """装配审批存储并执行一次人工决定。"""

    return DecideApproval(JsonApprovalRepository(approval_root)).execute(
        operation_key,
        decision,
        note=note,
    )


def respond_to_human_input(
    human_input_root: str,
    request_id: str,
    *,
    answer: str = "",
    cancel: bool = False,
    note: str = "",
) -> HumanInputRequest:
    """装配人工问题存储并保存回答或取消决定。"""

    return RespondToHumanInput(JsonHumanInputRepository(human_input_root)).execute(
        request_id,
        answer=answer,
        cancel=cancel,
        note=note,
    )


def prepare_continuation(
    run_dir: str,
    human_input_root: str,
    *,
    override_task: str = "",
    workspace: str = "",
) -> tuple[TaskCheckpoint, str, ContinuationPlan]:
    """从文件适配器加载 checkpoint，并构造新的显式 continuation。"""

    checkpoint_path = JsonTaskStateRepository.latest_path(run_dir)
    checkpoint = JsonTaskStateRepository.load_path(checkpoint_path)
    plan = BuildContinuationPlan(
        JsonHumanInputRepository(human_input_root)
    ).execute(
        checkpoint,
        override_task=override_task,
        workspace=workspace,
    )
    return checkpoint, str(checkpoint_path), plan


def latest_checkpoint_path(run_dir: str) -> str:
    """通过 Runtime 文件适配器定位一个 run 的最新 checkpoint。"""

    return str(JsonTaskStateRepository.latest_path(run_dir))

from agent_forge.runtime.config import RuntimeConfig  # noqa: E402

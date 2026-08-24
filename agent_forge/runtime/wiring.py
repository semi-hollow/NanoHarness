"""所有入站入口共用的 Runtime composition root。

系统角色：把 Config 与 Ports 绑定到具体 Adapter，再构造唯一的 canonical
``AgentLoop``；Application/Domain 均不在内部自行 new 基础设施对象。
输入：typed build request、Event/Model/Tool 边界与可选 overrides；输出：
``RuntimeDependencies``、``AgentLoop`` 或控制面 Repository。
相邻边界：Apps/Harness 只调用这里完成装配；``AgentLoop`` 从此处以后只依赖 Ports。

折叠导航：1 装配请求；2 Tool/Model Adapter；3 Runtime dependencies；
4 AgentLoop；5 控制面 Repository helper。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_forge.memory.adapters import JsonLongTermMemoryRepository
from agent_forge.memory.application import LongTermMemoryService
from agent_forge.memory.ports import LongTermMemoryRecallPort
from agent_forge.contracts import DEFAULT_TOOL_EXECUTION_TIMEOUT_SECONDS
from agent_forge.runtime.adapters.model_gateway import ModelGateway, RetryPolicy
from agent_forge.hooks import RuntimeHook
from agent_forge.runtime.adapters import (
    JsonApprovalRepository,
    JsonConversationThreadRepository,
    JsonHumanInputRepository,
    JsonOperationLedgerRepository,
    JsonTaskStateRepository,
    NoopRunControl,
    RepositorySystemContextAssembler,
)
from agent_forge.runtime.application.agent_loop import AgentLoop
from agent_forge.runtime.application.dependencies import RuntimeDependencies
from agent_forge.runtime.application.model_policy import resolve_model_capabilities
from agent_forge.runtime.application.operator_control import (
    DecideApproval,
    RespondToHumanInput,
)
from agent_forge.runtime.domain.approval import ApprovalRequest
from agent_forge.runtime.domain.human_input import HumanInputRequest
from agent_forge.runtime.domain.task import TaskCheckpoint
from agent_forge.runtime.adapters.execution_environment import ExecutionEnvironment
from agent_forge.runtime.adapters.execution_environment import ExecutionEnvironmentConfig
from agent_forge.runtime.adapters.hook_manager import HookManager
from agent_forge.runtime.adapters.openai_compatible import OpenAICompatibleLLMClient
from agent_forge.runtime.adapters.model_config import LLMConfig
from agent_forge.runtime.ports import (
    ApprovalRepository,
    ConversationThreadRepository,
    SystemContextAssemblerPort,
    EnvironmentPort,
    EventSink,
    HookPort,
    HumanInputRepository,
    ModelPort,
    OperationLedgerRepository,
    RunControlPort,
    SkillSelectorPort,
    TaskStateRepository,
    ToolGateway,
)
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.skills import build_default_skill_registry
from agent_forge.tools.builtins.create_file import CreateFileTool
from agent_forge.tools.builtins.replace_text import ReplaceTextTool
from agent_forge.tools.builtins.ask_human import AskHumanTool
from agent_forge.tools.builtins.python_validation import PythonValidationTool
from agent_forge.tools.builtins.git_diff import GitDiffTool
from agent_forge.tools.builtins.git_status import GitStatusTool
from agent_forge.tools.builtins.grep_search import GrepSearchTool
from agent_forge.tools.builtins.list_files import ListFilesTool
from agent_forge.tools.mcp.config import MCPConfigLoader
from agent_forge.tools.builtins.read_file import ReadFileTool
from agent_forge.tools.builtins.remember_memory import RememberMemoryTool
from agent_forge.tools.registry import ToolRegistry
from agent_forge.tools.builtins.run_command import RunCommandTool
from agent_forge.tools.builtins.write_file import WriteFileTool
from agent_forge.infrastructure.storage_layout import MEMORY_ROOT


# region 1. 装配请求：所有外围入口共享同一组 typed dependency contract
# 核心数据：装配受治理工具注册表所需的完整输入。
@dataclass(frozen=True, kw_only=True)
class ToolRegistryBuildRequest:
    """装配受治理工具注册表所需的完整输入。"""

    workspace: str
    auto: bool
    mcp_config_file: str | None = None
    mcp_allowed_tools: tuple[str, ...] = ()
    enabled_tools: tuple[str, ...] | None = None
    execution_environment: ExecutionEnvironment | None = None
    tool_execution_timeout_seconds: int = DEFAULT_TOOL_EXECUTION_TIMEOUT_SECONDS
    memory_root: str = str(MEMORY_ROOT)
    memory_namespace: str = ""

    def __post_init__(self) -> None:
        if self.tool_execution_timeout_seconds <= 0:
            raise ValueError("tool_execution_timeout_seconds must be positive")


# 核心数据：外围入口提交的一次人工回答或取消命令。
@dataclass(frozen=True, kw_only=True)
class HumanInputResponseCommand:
    """外围入口提交的一次人工回答或取消命令。"""

    human_input_root: str
    request_id: str
    answer: str = ""
    cancel: bool = False
    note: str = ""


@dataclass(frozen=True, kw_only=True)
class RuntimeDependencyOverrides:
    """供 SDK 或测试按端口替换默认 Adapter 的内部装配请求。

    未提供的字段仍由本模块创建默认实现。它不是第二套 Runtime，而是让所有入站入口
    继续共享同一个 composition root。
    """

    system_context_assembler: SystemContextAssemblerPort | None = None
    skills: SkillSelectorPort | None = None
    environment: EnvironmentPort | None = None
    hooks: HookPort | None = None
    additional_hooks: tuple[RuntimeHook, ...] = ()
    task_states: TaskStateRepository | None = None
    conversation_threads: ConversationThreadRepository | None = None
    approvals: ApprovalRepository | None = None
    human_inputs: HumanInputRepository | None = None
    operations: OperationLedgerRepository | None = None
    long_term_memory_recall: LongTermMemoryRecallPort | None = None
    control: RunControlPort | None = None


@dataclass(frozen=True, kw_only=True)
class AgentLoopBuildRequest:
    """带可选端口覆盖的完整 AgentLoop 装配请求。"""

    config: "RuntimeConfig"
    trace: EventSink
    registry: ToolGateway
    llm: ModelPort | None
    overrides: RuntimeDependencyOverrides | None = None
# endregion 1. 装配请求结束


# region 2. Tool / Model Adapter：只创建边界实现，不启动 AgentLoop
def build_registry(request: ToolRegistryBuildRequest) -> ToolRegistry:
    """构造 AgentLoop 使用的受治理工具注册表。"""

    sandbox = WorkspaceSandbox(request.workspace)
    registry = ToolRegistry()
    builtin_tools = [
        ListFilesTool(sandbox),
        ReadFileTool(sandbox),
        WriteFileTool(sandbox, request.auto),
        CreateFileTool(sandbox, request.auto),
        GrepSearchTool(sandbox),
        ReplaceTextTool(sandbox, request.auto),
        RunCommandTool(
            sandbox,
            request.auto,
            execution_environment=request.execution_environment,
            timeout_seconds=request.tool_execution_timeout_seconds,
        ),
        GitStatusTool(sandbox),
        GitDiffTool(sandbox),
        PythonValidationTool(
            sandbox,
            execution_environment=request.execution_environment,
            timeout_seconds=request.tool_execution_timeout_seconds,
        ),
        AskHumanTool(),
        RememberMemoryTool(
            memory_root=request.memory_root,
            project_namespace=(
                request.memory_namespace or str(Path(request.workspace).resolve())
            ),
        ),
    ]
    known_names = {tool.name for tool in builtin_tools}
    requested_names = (
        set(request.enabled_tools) if request.enabled_tools is not None else None
    )
    unknown_names = sorted((requested_names or set()) - known_names)
    if unknown_names:
        raise ValueError(f"unknown built-in tools: {', '.join(unknown_names)}")
    # enabled_tools 是 allowlist；未列出的 built-in 不注册，也不会出现在模型 schema 中。
    for tool in builtin_tools:
        if requested_names is not None and tool.name not in requested_names:
            continue
        registry.register(tool)
    if request.mcp_config_file:
        # MCP 工具仍进入同一个 Registry，后续继续经过相同 Routing/Authorization 主链。
        registry.mcp_config_report = MCPConfigLoader(sandbox).load_into(
            registry,
            request.mcp_config_file,
            allowed_tools=list(request.mcp_allowed_tools),
        )
    return registry


def build_llm(config: LLMConfig, *, max_attempts: int = 2) -> ModelGateway:
    """根据已解析配置构造带有界请求尝试次数的统一模型网关。"""

    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")

    if config.uses_openai_compatible_api:
        return ModelGateway(
            OpenAICompatibleLLMClient.from_config(config),
            provider=config.provider,
            model=config.model or "unknown",
            retry_policy=RetryPolicy(max_attempts=max_attempts),
            capabilities=config.capabilities,
        )
    raise ValueError(f"Unsupported LLM provider: {config.provider}")
# endregion 2. Tool / Model Adapter 结束


# region 3. Runtime dependencies：默认 Adapter 与显式 Port override 在一个位置收口
def build_runtime_dependencies(
    config: "RuntimeConfig",
    trace: EventSink,
    registry: ToolGateway,
    llm: ModelPort | None,
) -> RuntimeDependencies:
    """一次性装配 AgentLoop 需要的全部出站端口实现。

    CLI、benchmark 和 multi-agent 都必须通过这里装配 Runtime，避免不同入口偷偷
    创建不同的审批、恢复或幂等行为。
    """

    return _build_runtime_dependencies(
        AgentLoopBuildRequest(
            config=config,
            trace=trace,
            registry=registry,
            llm=llm,
        )
    )


def _build_runtime_dependencies(
    build_request: AgentLoopBuildRequest,
) -> RuntimeDependencies:
    """执行真正的 dependency binding；不运行模型或工具。

    伪代码：验证 Model -> 选择 Environment -> 选择/装配 Hook -> 为剩余 Port
    注入 override 或 canonical JSON/Memory Adapter -> 返回一份依赖集合。
    """

    runtime_config = build_request.config
    if build_request.llm is None:
        raise ValueError(
            "AgentLoop requires a real LLM client; build it through runtime.wiring"
        )
    dependency_overrides = build_request.overrides or RuntimeDependencyOverrides()
    execution_environment = (
        dependency_overrides.environment
        or runtime_config.execution_environment
        or ExecutionEnvironment(
            ExecutionEnvironmentConfig(workspace=runtime_config.workspace)
        )
    )
    # Hook 与 Environment 必须成对保持治理语义；custom Environment 不能偷用默认 Hook。
    if dependency_overrides.hooks is not None:
        if dependency_overrides.additional_hooks:
            raise ValueError(
                "additional lifecycle hooks cannot be combined with a full HookPort override"
            )
        runtime_hooks = dependency_overrides.hooks
    elif isinstance(execution_environment, ExecutionEnvironment):
        runtime_hooks = HookManager.default(
            execution_environment,
            runtime_config.auto_approve_writes,
            approval_mode=runtime_config.approval_mode,
            additional_hooks=list(dependency_overrides.additional_hooks),
        ).observe_with(build_request.trace)
    else:
        raise ValueError(
            "a custom EnvironmentPort requires a matching HookPort override"
        )
    return RuntimeDependencies(
        events=build_request.trace,
        system_context_assembler=(
            dependency_overrides.system_context_assembler
            or RepositorySystemContextAssembler()
        ),
        skills=dependency_overrides.skills
        or build_default_skill_registry(runtime_config.skill_manifest_files),
        tools=build_request.registry,
        model=build_request.llm,
        model_capabilities=resolve_model_capabilities(
            build_request.llm,
            runtime_config.model_capabilities,
            fallback_context_window=runtime_config.max_prompt_tokens,
        ),
        environment=execution_environment,
        hooks=runtime_hooks,
        task_states=dependency_overrides.task_states
        or JsonTaskStateRepository(runtime_config.task_state_root),
        conversation_threads=dependency_overrides.conversation_threads
        or JsonConversationThreadRepository(runtime_config.conversation_thread_root),
        approvals=dependency_overrides.approvals
        or JsonApprovalRepository(runtime_config.approval_root),
        human_inputs=dependency_overrides.human_inputs
        or JsonHumanInputRepository(runtime_config.human_input_root),
        operations=dependency_overrides.operations
        or JsonOperationLedgerRepository(runtime_config.operation_ledger_root),
        control=dependency_overrides.control or NoopRunControl(),
        long_term_memory_recall=dependency_overrides.long_term_memory_recall
        or LongTermMemoryService(
            JsonLongTermMemoryRepository(runtime_config.memory_root or str(MEMORY_ROOT))
        ),
    )
# endregion 3. Runtime dependencies 结束


# region 4. Canonical AgentLoop：Single、Benchmark 与 Multi-Agent Worker 共用此路径
# 主要入口：为所有入站路径装配同一套单 Agent Runtime 和治理端口。
def build_agent_loop(
    config: "RuntimeConfig",
    trace: EventSink,
    registry: ToolGateway,
    llm: ModelPort | None,
) -> AgentLoop:
    """把入站 Adapter 提供的实现装配为规范 ``AgentLoop``。

    上游只能提交 RuntimeConfig 与四个边界端口；下一 owner 是
    ``build_agent_loop_from_request``。本函数不运行任务、不创建 artifact，也不
    推断状态。系统不变量是 CLI、SDK、benchmark 与 worker 必须复用同一 composition
    path，不能各自复制 Runtime 默认值。
    """

    return build_agent_loop_from_request(
        AgentLoopBuildRequest(
            config=config,
            trace=trace,
            registry=registry,
            llm=llm,
        )
    )


def build_agent_loop_from_request(request: AgentLoopBuildRequest) -> AgentLoop:
    """按类型化请求装配允许端口覆盖的标准单 Agent 用例。"""

    return AgentLoop(request.config, _build_runtime_dependencies(request))
# endregion 4. Canonical AgentLoop 结束


# region 5. 控制面 Repository helper：外围入口复用类型化 Adapter，不复制业务流程
def build_task_state_repository(root: str | Path) -> TaskStateRepository:
    """为 SDK facade 创建默认 JSON checkpoint repository。"""

    return JsonTaskStateRepository(root)


def build_conversation_thread_repository(
    root: str | Path,
) -> ConversationThreadRepository:
    """为 Harness/Console 创建 canonical Thread repository。"""

    return JsonConversationThreadRepository(root)


def build_approval_repository(root: str | Path) -> ApprovalRepository:
    """为控制面创建默认 JSON approval repository。"""

    return JsonApprovalRepository(root)


def build_human_input_repository(root: str | Path) -> HumanInputRepository:
    """为控制面创建默认 JSON human-input repository。"""

    return JsonHumanInputRepository(root)


def decide_approval(
    approval_root: str,
    operation_key: str,
    decision: str,
    *,
    note: str = "",
) -> ApprovalRequest:
    """装配审批存储并执行一次人工决定。"""

    return DecideApproval(JsonApprovalRepository(approval_root)).decide(
        operation_key,
        decision,
        note=note,
    )


def respond_to_human_input(
    command: HumanInputResponseCommand,
) -> HumanInputRequest:
    """装配人工问题存储并保存回答或取消决定。"""

    return RespondToHumanInput(
        JsonHumanInputRepository(command.human_input_root)
    ).respond(
        command.request_id,
        answer=command.answer,
        cancel=command.cancel,
        note=command.note,
    )


def latest_checkpoint_path(run_dir: str) -> str:
    """通过 Runtime 文件适配器定位一个 run 的最新 checkpoint。"""

    return str(JsonTaskStateRepository.latest_path(run_dir))


def load_task_checkpoint(path: str) -> TaskCheckpoint:
    """通过 Runtime 文件适配器加载一个类型化 checkpoint。"""

    return JsonTaskStateRepository.load_path(path)


def list_pending_human_inputs(root: str) -> list[HumanInputRequest]:
    """查询指定控制面目录中的待回答问题。"""

    return JsonHumanInputRepository(root).list_pending()


def list_pending_approvals(root: str) -> list[ApprovalRequest]:
    """查询指定控制面目录中尚未执行的待审批操作。"""

    return JsonApprovalRepository(root).list_pending()
# endregion 5. 控制面 Repository helper 结束


from agent_forge.runtime.config import RuntimeConfig  # noqa: E402

"""AgentLoop Runtime 的类型化输入配置。

字段按职责排列：workspace 与循环预算、恢复与人工控制、Skill/Tool 策略、
长期记忆。Standalone、Fanout Worker 与 Finalizer 复用该配置类型，但由 composition
显式选择不同的 System Prompt profile。
新增运行策略应先归入这些分组，避免把任意 CLI 字段散落进 AgentLoop。
"""

from dataclasses import dataclass, field
from typing import Any

from agent_forge.contracts import (
    DEFAULT_MAX_CONTEXT_CHARS,
    DEFAULT_MAX_PROMPT_TOKENS,
    DEFAULT_MAX_STEPS,
    DEFAULT_TOOL_EXECUTION_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
)
from agent_forge.runtime.domain.model import ModelCapabilities


# 核心数据：一次 AgentLoop run 的全部外部策略与资源位置。
@dataclass
class RuntimeConfig:
    """Runtime 的单一配置对象。

    ``workspace`` 指任务代码根目录；``max_*``、``timeout_seconds`` 和
    ``cost_budget_usd`` 控制循环资源；``*_root`` 指 durable 控制面目录；
    ``approval_mode``、Skill 和 tool routing 字段控制治理策略；最后三个 memory
    字段控制证据记忆的存储、隔离 namespace 和完整记录字符预算。
    """

    # 工作区与 AgentLoop 资源预算。
    workspace: str
    max_steps: int = DEFAULT_MAX_STEPS
    auto_approve_writes: bool = True
    trace_file: str = "agent_forge_trace.json"
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS
    max_prompt_tokens: int = DEFAULT_MAX_PROMPT_TOKENS
    reserved_output_tokens: int = 4_096
    max_consecutive_failures: int = 3
    max_consecutive_identical_tool_calls: int = 2
    max_tool_calls_per_turn: int = 4
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    tool_execution_timeout_seconds: int = DEFAULT_TOOL_EXECUTION_TIMEOUT_SECONDS
    cost_budget_usd: float | None = None
    previous_task: str = ""
    session_summary: str = ""
    execution_environment: Any | None = None
    model_capabilities: ModelCapabilities | None = None

    # Checkpoint、人工控制和状态变更操作幂等状态的持久化位置。
    task_state_root: str = ".agent_forge/runs/embedded/task_state"
    resume_state: str = ""
    approval_root: str = ".agent_forge/internal/state/approvals"
    human_input_root: str = ".agent_forge/internal/state/human_input"
    human_thread_id: str = ""
    operation_ledger_root: str = ".agent_forge/internal/state/operation_ledger"
    approval_mode: str = "trusted"

    # 模型可见 Skill 与工具集合策略。
    skill_mode: str = "auto"
    skill_names: list[str] = field(default_factory=list)
    skill_manifest_files: list[str] = field(default_factory=list)
    tool_routing_mode: str = "task-aware"
    # AgentLoop 复用同一控制流，但 System Prompt 必须准确表达当前执行角色。
    system_prompt_profile: str = "single_agent"

    # 分层项目指令发现与 runtime override。
    instruction_target: str = ""
    global_instruction_files: list[str] = field(default_factory=list)
    runtime_instructions: str = ""
    instruction_max_bytes: int = 2_600

    # 证据长期记忆；working memory 和 ConversationHistoryDigest 不由这三个字段持久化。
    memory_root: str = ""
    memory_namespace: str = ""
    memory_max_chars: int = 2_000

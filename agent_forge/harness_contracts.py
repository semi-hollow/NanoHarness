"""Harness Public API 使用的数据契约。

学习主流程时可以跳过本文件；这里只定义配置、输入、输出和可替换扩展点，
不执行 AgentLoop，也不包含文件系统副作用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agent_forge.context.ports import LongTermMemoryRecallPort
from agent_forge.contracts import JsonObject
from agent_forge.hooks import RuntimeHook
from agent_forge.observability.adapters.streaming import EventStreamPolicy
from agent_forge.observability.ports import RuntimeEventListener
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.domain.task import TaskCheckpoint, TaskRunStatus
from agent_forge.runtime.ports import (
    ApprovalRepository,
    ContextAssemblerPort,
    EnvironmentPort,
    EventSink,
    HookPort,
    HumanInputRepository,
    OperationLedgerRepository,
    RunControlPort,
    SkillSelectorPort,
    TaskStateRepository,
)


@dataclass(frozen=True)
class HarnessConfig:
    """可跨多次运行复用的 Runtime 策略，不包含模型密钥。"""

    workspace: str = "."
    output_root: str = ".agent_forge/runs"
    max_steps: int = 16
    max_context_chars: int = 12_000
    max_prompt_tokens: int = 32_768
    reserved_output_tokens: int = 4_096
    max_consecutive_failures: int = 3
    max_tool_repeats: int = 2
    max_tool_calls_per_turn: int = 4
    timeout_seconds: float = 900.0
    cost_budget_usd: float | None = None
    approval_mode: str = "trusted"
    auto_approve_writes: bool = True
    skill_mode: str = "auto"
    skill_names: tuple[str, ...] = ()
    skill_manifest_files: tuple[str, ...] = ()
    tool_routing_mode: str = "task-aware"
    enabled_tools: tuple[str, ...] | None = None
    mcp_config_file: str | None = None
    mcp_allowed_tools: tuple[str, ...] = ()
    memory_recall_limit: int = 6
    model_capabilities: ModelCapabilities | None = None
    instruction_target: str = ""
    global_instruction_files: tuple[str, ...] = ()
    runtime_instructions: str = ""
    instruction_max_bytes: int = 2_600
    approval_root: str = ""
    human_input_root: str = ""
    operation_ledger_root: str = ""
    memory_root: str = ""
    execution_mode: str = "local"
    network_policy: str = "deny"
    keep_worktree: bool = True
    container_runtime: str = "docker"
    container_image: str = "python:3.11-slim"
    container_cpus: float = 1.0
    container_memory: str = "1g"
    container_pids_limit: int = 256
    container_read_only: bool = True

    def __post_init__(self) -> None:
        """尽早拒绝无法安全进入 Runtime 的配置。"""

        if self.max_steps < 1:
            raise ValueError("max_steps must be positive")
        if self.max_context_chars < 1 or self.max_prompt_tokens < 1:
            raise ValueError("context budgets must be positive")
        if not 0 <= self.reserved_output_tokens < self.max_prompt_tokens:
            raise ValueError("reserved_output_tokens must be below max_prompt_tokens")
        if self.approval_mode not in {
            "trusted",
            "on-write",
            "on-risk",
            "locked",
            "dry-run",
        }:
            raise ValueError(f"unsupported approval_mode: {self.approval_mode}")
        if self.tool_routing_mode not in {"task-aware", "all"}:
            raise ValueError(f"unsupported tool_routing_mode: {self.tool_routing_mode}")
        if self.skill_mode not in {"auto", "none"}:
            raise ValueError(f"unsupported skill_mode: {self.skill_mode}")
        if self.max_consecutive_failures < 1:
            raise ValueError("max_consecutive_failures must be positive")
        if self.max_tool_repeats < 0:
            raise ValueError("max_tool_repeats must not be negative")
        if self.max_tool_calls_per_turn < 1:
            raise ValueError("max_tool_calls_per_turn must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.cost_budget_usd is not None and self.cost_budget_usd < 0:
            raise ValueError("cost_budget_usd must not be negative")
        if self.memory_recall_limit < 0:
            raise ValueError("memory_recall_limit must not be negative")
        if self.instruction_max_bytes < 1:
            raise ValueError("instruction_max_bytes must be positive")
        if self.execution_mode not in {"local", "worktree", "container"}:
            raise ValueError(f"unsupported execution_mode: {self.execution_mode}")
        if self.network_policy not in {"deny", "allow"}:
            raise ValueError(f"unsupported network_policy: {self.network_policy}")


@dataclass(frozen=True)
class RunRequest:
    """一次 Public API 运行的最小输入；空字段继承 ``HarnessConfig``。"""

    task: str
    workspace: str = ""
    output_root: str = ""
    agent_name: str = "CodingAgent"
    resume_state: str = ""
    human_thread_id: str = ""
    resolved_config: JsonObject | None = None

    def validate(self) -> None:
        """验证调用方必须明确提供的字段。"""

        if not self.task.strip():
            raise ValueError("task must not be empty")
        if not self.agent_name.strip():
            raise ValueError("agent_name must not be empty")


@dataclass(frozen=True)
class RunResult:
    """一次运行的类型化结论和可追溯 artifact 入口。"""

    run_id: str
    status: TaskRunStatus
    stop_reason: str
    final_answer: str
    artifact_dir: Path
    checkpoint: TaskCheckpoint
    trace_path: Path | None = None
    usage_path: Path | None = None
    patch_path: Path | None = None
    manifest_path: Path | None = None

    @property
    def waiting_for_operator(self) -> bool:
        """返回运行是否停在人工回答或审批边界。"""

        return self.status in {
            TaskRunStatus.WAITING_APPROVAL,
            TaskRunStatus.WAITING_HUMAN,
            TaskRunStatus.PAUSED,
        }


class EventSinkFactory(Protocol):
    """为每次 run 创建独立 EventSink，避免复用 run identity。"""

    def create(self, trace_path: str) -> EventSink:
        """返回尚未发布、具有独立 ``run_id`` 的 sink。"""


@dataclass(frozen=True)
class HarnessExtensions:
    """高级调用方可以替换的稳定 Port 集合；未提供项使用内置 Adapter。"""

    context_assembler: ContextAssemblerPort | None = None
    checkpoint_repository: TaskStateRepository | None = None
    event_sink_factory: EventSinkFactory | None = None
    event_listeners: tuple[RuntimeEventListener, ...] = ()
    event_stream_policy: EventStreamPolicy = field(default_factory=EventStreamPolicy)
    execution_environment: EnvironmentPort | None = None
    skill_selector: SkillSelectorPort | None = None
    approval_repository: ApprovalRepository | None = None
    human_input_repository: HumanInputRepository | None = None
    operation_repository: OperationLedgerRepository | None = None
    long_term_memory_recall: LongTermMemoryRecallPort | None = None
    run_control: RunControlPort | None = None
    lifecycle_hooks: tuple[RuntimeHook, ...] = ()
    hook_policy: HookPort | None = None


__all__ = [
    "EventSinkFactory",
    "HarnessConfig",
    "HarnessExtensions",
    "RunRequest",
    "RunResult",
]

"""单 Agent Run attempt 的 checkpoint 领域状态。

系统角色：保存恢复执行所需的最小 Run 状态与 pending ToolCall cursor；Thread/Turn 拥有
任务和 Conversation，Repository 拥有落盘。
输入：``TaskStartRequest`` / ``TaskCheckpointUpdate``；输出：canonical v4 checkpoint。
相邻边界：RunLifecycle 决定何时迁移；本 Domain 校验单向字段更新；JSON Adapter 持久化。

折叠导航：1 serialized contract/status；2 pending execution pointer；3 update contract；
4 checkpoint transition；5 canonical serialization。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, TypedDict

from agent_forge.contracts import JsonObject


# region 1. Serialized contract 与 Run statuses
class PendingExecutionPointerData(TypedDict):
    """Checkpoint 指向 canonical assistant item 的同 Turn tool-batch cursor。"""

    assistant_item_id: str
    next_tool_call_index: int
    pending_operation_key: str
    pending_operation_fingerprint: JsonObject


class TaskCheckpointData(TypedDict):
    """Trace、CLI 和恢复流程共享的 canonical v4 序列化契约。"""

    schema_version: int
    run_id: str
    thread_id: str
    turn_id: str
    context_revision: int
    workspace: str
    execution_workspace: str
    execution_mode: str
    status: str
    current_step: int
    agent_name: str
    last_tool: str
    last_observation: str
    stop_reason: str
    stop_output: str | None
    final_answer: str | None
    resume_hint: str
    messages_count: int
    observations_count: int
    pending_execution: PendingExecutionPointerData | None
    updated_at: float
    created_at: float
    metadata: JsonObject


class TaskRunStatus(Enum):
    """一次 Agent Run 允许出现的生命周期状态。"""

    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_HUMAN = "waiting_human"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"


RESUMABLE_RUN_STATUSES = frozenset(
    {
        TaskRunStatus.CREATED.value,
        TaskRunStatus.RUNNING.value,
        TaskRunStatus.WAITING_APPROVAL.value,
        TaskRunStatus.WAITING_HUMAN.value,
        TaskRunStatus.PAUSED.value,
    }
)
# endregion 1. Contract 与 statuses 结束


# region 2. Pending execution pointer：恢复同一 Assistant batch，不重新问模型
@dataclass(frozen=True, kw_only=True)
class PendingExecutionPointer:
    """未完成 tool batch 的最小恢复指针；assistant payload 只存 Thread journal。"""

    assistant_item_id: str
    next_tool_call_index: int = 0
    pending_operation_key: str = ""
    pending_operation_fingerprint: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.assistant_item_id.strip():
            raise ValueError("pending execution requires assistant_item_id")
        if self.next_tool_call_index < 0:
            raise ValueError("pending tool call index must not be negative")

    def to_dict(self) -> PendingExecutionPointerData:
        return {
            "assistant_item_id": self.assistant_item_id,
            "next_tool_call_index": self.next_tool_call_index,
            "pending_operation_key": self.pending_operation_key,
            "pending_operation_fingerprint": self.pending_operation_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PendingExecutionPointer":
        raw_fingerprint = value.get("pending_operation_fingerprint")
        return cls(
            assistant_item_id=str(value.get("assistant_item_id") or ""),
            next_tool_call_index=int(value.get("next_tool_call_index") or 0),
            pending_operation_key=str(value.get("pending_operation_key") or ""),
            pending_operation_fingerprint=(
                dict(raw_fingerprint) if isinstance(raw_fingerprint, dict) else {}
            ),
        )


@dataclass(frozen=True)
class _PendingExecutionUnchanged:
    pass


PENDING_EXECUTION_UNCHANGED = _PendingExecutionUnchanged()
# endregion 2. Pending execution pointer 结束


# region 3. Start / Update 类型化命令
@dataclass(frozen=True)
class TaskStartRequest:
    """一次新 Run 的 Thread/Turn、工作区和初始元数据。"""

    run_id: str
    thread_id: str
    turn_id: str
    workspace: str
    execution_workspace: str
    execution_mode: str
    agent_name: str
    context_revision: int = 0
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class TaskCheckpointUpdate:
    """Checkpoint 的类型化 patch；Thread 内容不在此对象中复制。"""

    status: TaskRunStatus | str | None = None
    current_step: int | None = None
    context_revision: int | None = None
    last_tool: str | None = None
    last_observation: str | None = None
    stop_reason: str | None = None
    stop_output: str | None = None
    final_answer: str | None = None
    resume_hint: str | None = None
    messages_count: int | None = None
    observations_count: int | None = None
    pending_execution: (
        PendingExecutionPointer | None | _PendingExecutionUnchanged
    ) = PENDING_EXECUTION_UNCHANGED
    metadata: JsonObject | None = None
    updated_at: float | None = None

    def status_value(self) -> str | None:
        if isinstance(self.status, TaskRunStatus):
            return self.status.value
        return self.status
# endregion 3. Start / Update command 结束


# region 4. Checkpoint identity 与显式字段 transition
@dataclass
class TaskCheckpoint:
    """一次 Run 的最小执行恢复快照。

    Thread/Turn 拥有 root task 与 Conversation；``context_revision`` 指向独立
    ``context_state.json``；``pending_execution`` 指向 authoritative assistant item。
    ``workspace`` 始终是用户项目路径，``execution_workspace`` 才是本 Turn 当前实际
    执行树。Production loader 只接受 v4，不保留 v3 fallback。
    """

    SCHEMA_VERSION: ClassVar[int] = 4

    run_id: str
    thread_id: str
    turn_id: str
    workspace: str
    execution_workspace: str
    execution_mode: str
    status: str
    context_revision: int = 0
    current_step: int = 0
    agent_name: str = "CodingAgent"
    last_tool: str = ""
    last_observation: str = ""
    stop_reason: str = ""
    stop_output: str | None = None
    final_answer: str | None = None
    resume_hint: str = ""
    messages_count: int = 0
    observations_count: int = 0
    pending_execution: PendingExecutionPointer | None = None
    updated_at: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id or not self.thread_id or not self.turn_id:
            raise ValueError("checkpoint run/thread/turn identity is required")
        if self.context_revision < 0:
            raise ValueError("checkpoint context_revision must not be negative")
        if not self.workspace or not self.execution_workspace:
            raise ValueError("checkpoint requested and execution workspaces are required")
        if self.execution_mode not in {"local", "worktree", "container"}:
            raise ValueError(f"unsupported checkpoint execution mode: {self.execution_mode}")

    def apply_transition(self, update: TaskCheckpointUpdate) -> None:
        """应用一次显式执行状态转换；持久化由 Repository 在调用后完成。"""

        # Update 是 patch contract：只有非 None/非 UNCHANGED 字段改变当前快照。
        next_task_status = update.status_value()
        if next_task_status is not None:
            self.status = next_task_status
        if update.current_step is not None:
            self.current_step = update.current_step
        if update.context_revision is not None:
            if update.context_revision < self.context_revision:
                raise ValueError("checkpoint context_revision must not move backwards")
            self.context_revision = update.context_revision
        if update.last_tool is not None:
            self.last_tool = update.last_tool
        if update.last_observation is not None:
            self.last_observation = update.last_observation
        if update.stop_reason is not None:
            self.stop_reason = update.stop_reason
        if update.stop_output is not None:
            self.stop_output = update.stop_output
        if update.final_answer is not None:
            self.final_answer = update.final_answer
        if update.resume_hint is not None:
            self.resume_hint = update.resume_hint
        if update.messages_count is not None:
            self.messages_count = update.messages_count
        if update.observations_count is not None:
            self.observations_count = update.observations_count
        if not isinstance(update.pending_execution, _PendingExecutionUnchanged):
            self.pending_execution = update.pending_execution
        if update.metadata is not None:
            self.metadata = update.metadata
        self.updated_at = (
            update.updated_at if update.updated_at is not None else time.time()
        )
    # endregion 4. Checkpoint transition 结束

    # region 5. Canonical v4 serialization：production loader 不带 legacy fallback
    def to_dict(self) -> TaskCheckpointData:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "context_revision": self.context_revision,
            "workspace": self.workspace,
            "execution_workspace": self.execution_workspace,
            "execution_mode": self.execution_mode,
            "status": self.status,
            "current_step": self.current_step,
            "agent_name": self.agent_name,
            "last_tool": self.last_tool,
            "last_observation": self.last_observation,
            "stop_reason": self.stop_reason,
            "stop_output": self.stop_output,
            "final_answer": self.final_answer,
            "resume_hint": self.resume_hint,
            "messages_count": self.messages_count,
            "observations_count": self.observations_count,
            "pending_execution": (
                self.pending_execution.to_dict()
                if self.pending_execution is not None
                else None
            ),
            "updated_at": self.updated_at,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskCheckpoint":
        """只加载 canonical v4；历史 artifact 必须离线迁移。"""

        schema_version = int(data.get("schema_version") or 0)
        if schema_version != cls.SCHEMA_VERSION:
            raise ValueError(
                "unsupported task checkpoint schema_version: "
                f"{schema_version}; migrate artifact to version {cls.SCHEMA_VERSION}"
            )
        payload: dict[str, Any] = dict(data)
        payload.pop("schema_version", None)
        raw_pending = payload.get("pending_execution")
        payload["pending_execution"] = (
            PendingExecutionPointer.from_dict(dict(raw_pending))
            if isinstance(raw_pending, dict)
            else None
        )
        return cls(**payload)
    # endregion 5. Canonical serialization 结束


__all__ = [
    "PENDING_EXECUTION_UNCHANGED",
    "PendingExecutionPointer",
    "PendingExecutionPointerData",
    "RESUMABLE_RUN_STATUSES",
    "TaskCheckpoint",
    "TaskCheckpointData",
    "TaskCheckpointUpdate",
    "TaskRunStatus",
    "TaskStartRequest",
]

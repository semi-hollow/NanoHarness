"""单 Agent 任务的领域状态，不包含持久化实现。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TypedDict

from agent_forge.contracts import JsonObject


class TaskCheckpointData(TypedDict):
    """Trace、CLI 和恢复流程共享的序列化契约。"""

    run_id: str
    task: str
    workspace: str
    status: str
    current_step: int
    agent_name: str
    last_tool: str
    last_observation: str
    stop_reason: str
    final_answer: str
    resume_hint: str
    messages_count: int
    observations_count: int
    context_digest: JsonObject
    updated_at: float
    created_at: float
    metadata: JsonObject


class TaskRunStatus(Enum):
    """一次 Agent 任务允许出现的生命周期状态。"""

    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_HUMAN = "waiting_human"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"


# 核心数据：创建首个 durable checkpoint 所需的完整输入。
@dataclass(frozen=True)
class TaskStartRequest:
    """一次新 run 的身份、工作区和初始元数据。"""

    run_id: str
    task: str
    workspace: str
    agent_name: str
    metadata: JsonObject = field(default_factory=dict)


# 核心数据：一次 checkpoint 状态迁移中允许修改的字段。
@dataclass(frozen=True)
class TaskCheckpointUpdate:
    """Checkpoint 的类型化 patch，未提供的字段保持原值。

    Application、Repository Port 和 JSON Adapter 共享此对象，避免三层分别维护
    一份长关键字参数表。``status`` 接受 enum 或持久化字符串，由领域对象统一归一化。
    """

    status: TaskRunStatus | str | None = None
    current_step: int | None = None
    last_tool: str | None = None
    last_observation: str | None = None
    stop_reason: str | None = None
    final_answer: str | None = None
    resume_hint: str | None = None
    messages_count: int | None = None
    observations_count: int | None = None
    context_digest: JsonObject | None = None
    metadata: JsonObject | None = None
    updated_at: float | None = None

    def status_value(self) -> str | None:
        """返回 checkpoint 使用的稳定字符串状态。"""

        if isinstance(self.status, TaskRunStatus):
            return self.status.value
        return self.status


# 核心数据：暂停、恢复和终态报告共享的 durable 任务快照。
@dataclass
class TaskCheckpoint:
    """可恢复任务的最小控制面快照。

    ``run_id/task/workspace/agent_name`` 标识运行；``status/current_step`` 是状态机
    位置；``last_*``、``stop_reason`` 和 ``resume_hint`` 指导恢复；计数字段与
    ``context_digest`` 保存有界上下文；``metadata`` 承载人工请求和执行环境等
    扩展事实。Repository 只负责保存和加载。完整消息和工具输出属于 Trace，
    不进入本对象。
    """

    run_id: str
    task: str
    workspace: str
    status: str
    current_step: int = 0
    agent_name: str = "CodingAgent"
    last_tool: str = ""
    last_observation: str = ""
    stop_reason: str = ""
    final_answer: str = ""
    resume_hint: str = ""
    messages_count: int = 0
    observations_count: int = 0
    context_digest: JsonObject = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    metadata: JsonObject = field(default_factory=dict)

    def apply_transition(self, update: TaskCheckpointUpdate) -> None:
        """应用一次显式字段转换；持久化由 Repository 在调用后完成。"""

        status = update.status_value()
        if status is not None:
            self.status = status
        if update.current_step is not None:
            self.current_step = update.current_step
        if update.last_tool is not None:
            self.last_tool = update.last_tool
        if update.last_observation is not None:
            self.last_observation = update.last_observation
        if update.stop_reason is not None:
            self.stop_reason = update.stop_reason
        if update.final_answer is not None:
            self.final_answer = update.final_answer
        if update.resume_hint is not None:
            self.resume_hint = update.resume_hint
        if update.messages_count is not None:
            self.messages_count = update.messages_count
        if update.observations_count is not None:
            self.observations_count = update.observations_count
        if update.context_digest is not None:
            self.context_digest = update.context_digest
        if update.metadata is not None:
            self.metadata = update.metadata
        self.updated_at = (
            update.updated_at if update.updated_at is not None else time.time()
        )

    def to_dict(self) -> TaskCheckpointData:
        """返回稳定、可写入 JSON 的 checkpoint 结构。"""

        return {
            "run_id": self.run_id,
            "task": self.task,
            "workspace": self.workspace,
            "status": self.status,
            "current_step": self.current_step,
            "agent_name": self.agent_name,
            "last_tool": self.last_tool,
            "last_observation": self.last_observation,
            "stop_reason": self.stop_reason,
            "final_answer": self.final_answer,
            "resume_hint": self.resume_hint,
            "messages_count": self.messages_count,
            "observations_count": self.observations_count,
            "context_digest": self.context_digest,
            "updated_at": self.updated_at,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


def summarize_checkpoint(checkpoint: TaskCheckpoint, max_chars: int = 1400) -> str:
    """生成供 continuation 使用的紧凑、确定性上下文。"""

    summary = (
        f"resume_from_run={checkpoint.run_id}\n"
        f"previous_status={checkpoint.status}\n"
        f"previous_task={checkpoint.task}\n"
        f"last_tool={checkpoint.last_tool}\n"
        f"last_observation={checkpoint.last_observation}\n"
        f"stop_reason={checkpoint.stop_reason}\n"
        f"resume_hint={checkpoint.resume_hint}\n"
        f"context_digest={json.dumps(checkpoint.context_digest, ensure_ascii=False)}\n"
        f"final_answer={checkpoint.final_answer}"
    )
    if len(summary) <= max_chars:
        return summary
    return summary[: max_chars - 14] + " [compressed]"

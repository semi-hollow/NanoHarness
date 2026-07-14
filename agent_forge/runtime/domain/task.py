"""单 Agent 任务的领域状态，不包含持久化实现。"""

from __future__ import annotations

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
    updated_at: float
    created_at: float
    metadata: JsonObject


class TaskRunStatus(Enum):
    """一次 Agent 任务允许出现的生命周期状态。"""

    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_HUMAN = "waiting_human"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclass
class TaskCheckpoint:
    """可恢复任务的最小控制面快照。

    该对象拥有 checkpoint 字段及转换语义。Repository 只负责保存和加载，不应
    重新解释状态含义。完整消息和工具输出属于 Trace，不进入本对象。
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
    updated_at: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    metadata: JsonObject = field(default_factory=dict)

    def apply_transition(
        self,
        *,
        status: str | None = None,
        current_step: int | None = None,
        last_tool: str | None = None,
        last_observation: str | None = None,
        stop_reason: str | None = None,
        final_answer: str | None = None,
        resume_hint: str | None = None,
        messages_count: int | None = None,
        observations_count: int | None = None,
        metadata: JsonObject | None = None,
        updated_at: float | None = None,
    ) -> None:
        """应用一次显式字段转换；持久化由 Repository 在调用后完成。"""

        if status is not None:
            self.status = status
        if current_step is not None:
            self.current_step = current_step
        if last_tool is not None:
            self.last_tool = last_tool
        if last_observation is not None:
            self.last_observation = last_observation
        if stop_reason is not None:
            self.stop_reason = stop_reason
        if final_answer is not None:
            self.final_answer = final_answer
        if resume_hint is not None:
            self.resume_hint = resume_hint
        if messages_count is not None:
            self.messages_count = messages_count
        if observations_count is not None:
            self.observations_count = observations_count
        if metadata is not None:
            self.metadata = metadata
        self.updated_at = updated_at if updated_at is not None else time.time()

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
        f"final_answer={checkpoint.final_answer}"
    )
    if len(summary) <= max_chars:
        return summary
    return summary[: max_chars - 14] + " [compressed]"

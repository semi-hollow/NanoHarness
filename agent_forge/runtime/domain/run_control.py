"""运行中人工控制的领域信号。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from agent_forge.contracts import JsonObject


class RunControlKind(Enum):
    """Runtime 在安全边界上能够处理的控制动作。"""

    PAUSE = "pause"
    CANCEL = "cancel"
    STEER = "steer"


# 核心数据：操作员提交给运行中 AgentLoop 的一次控制信号。
@dataclass(frozen=True, kw_only=True)
class RunControlSignal:
    """``kind`` 标识动作，``message`` 携带 steer 内容，``reason`` 用于审计。"""

    kind: RunControlKind
    reason: str = ""
    message: str = ""
    requested_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.kind == RunControlKind.STEER and not self.message.strip():
            raise ValueError("steer message must not be empty")

    def to_dict(self) -> JsonObject:
        """返回不包含运行时对象的稳定 JSON 结构。"""

        return {
            "kind": self.kind.value,
            "reason": self.reason,
            "message": self.message,
            "requested_at": self.requested_at,
        }


@dataclass(frozen=True, kw_only=True)
class RuntimeCoordinationSignal:
    """由 Runtime 路由给 Worker 的非人工语义证据。"""

    event_id: str
    content: str
    plan_generation_id: str
    worker_attempt_id: int
    publisher_task_id: str
    target_task_id: str
    event_type: str
    semantic_key: str
    version: int
    human_authority: bool = False

    def to_dict(self) -> JsonObject:
        return {
            "event_id": self.event_id,
            "plan_generation_id": self.plan_generation_id,
            "worker_attempt_id": self.worker_attempt_id,
            "publisher_task_id": self.publisher_task_id,
            "target_task_id": self.target_task_id,
            "event_type": self.event_type,
            "semantic_key": self.semantic_key,
            "version": self.version,
            "human_authority": self.human_authority,
        }

"""运行时控制与协调的领域信号。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from agent_forge.contracts import JsonObject


RUNTIME_COORDINATION_EVIDENCE_PREFIX = (
    "[RUNTIME COORDINATION EVIDENCE]\n"
    "human_authority=false\n"
)


class RunControlKind(Enum):
    """操作员在 Runtime 安全边界可提交的控制动作。"""

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
        # STEER 会进入下一 Model Step 输入，空消息无法表达新的操作员方向。
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

    def __post_init__(self) -> None:
        """拒绝把 Worker 协调证据伪装成人工授权。"""

        # Runtime coordination 只能提供 peer evidence，永远不能升级为 human authority。
        if self.human_authority:
            raise ValueError("runtime coordination cannot carry human authority")

    def to_dict(self) -> JsonObject:
        """只投影协调身份与版本，模型输入内容不重复写入控制元数据。"""

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

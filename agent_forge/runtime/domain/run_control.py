"""运行中人工控制的领域信号。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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

    def to_dict(self) -> dict[str, object]:
        """返回不包含运行时对象的稳定 JSON 结构。"""

        return {
            "kind": self.kind.value,
            "reason": self.reason,
            "message": self.message,
            "requested_at": self.requested_at,
        }


@dataclass(frozen=True, kw_only=True)
class RuntimeCoordinationSignal:
    """Runtime 在模型安全边界投递的一条非人工协作证据。

    该对象与 ``RunControlSignal`` 分离，避免 peer-agent evidence 被审计为
    operator direction。``provenance`` 保存已由上游 Runtime 校验的事件身份；这里
    只负责把证据带入下一次模型输入，不重新决定路由或版本合法性。
    """

    source: str
    message: str
    provenance: dict[str, Any]
    requested_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("coordination signal source must not be empty")
        if not self.message.strip():
            raise ValueError("coordination signal message must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "message": self.message,
            "provenance": dict(self.provenance),
            "requested_at": self.requested_at,
        }

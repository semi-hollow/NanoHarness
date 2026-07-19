"""嵌入式 UI、IDE 和远程控制面消费的实时事件 envelope。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


# 核心数据：从内部 Evidence Event 投影出的脱敏实时事件。
@dataclass(frozen=True)
class RuntimeEvent:
    """``name/run_id/sequence`` 提供稳定排序，其余字段描述安全的运行事实。"""

    name: str
    run_id: str
    sequence: int
    step: int
    agent_name: str
    success: bool = True
    payload: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """返回 JSON/stream-json 可直接序列化的结构。"""

        return {
            "name": self.name,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "step": self.step,
            "agent_name": self.agent_name,
            "success": self.success,
            "payload": dict(self.payload),
        }

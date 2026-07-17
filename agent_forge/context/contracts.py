"""Context 能力的稳定契约。

Runtime 只需要读取有限的 memory 视图；检索和压缩仍由 Context 负责。Protocol 避免
上下文组装依赖具体的进程内 ``WorkingMemory`` 实现。
"""

from __future__ import annotations

from typing import Protocol

from agent_forge.context.domain import LongTermMemoryRecord


class ContextMemory(Protocol):
    """上下文选择真正需要的最小 memory 视图。"""

    def recent(self) -> list[object]:
        """返回有界的最近记录。"""

    def get(self, key: str, default: object = None) -> object:
        """读取一个已保存事实。"""

    def summary(self, max_chars: int = 800) -> str:
        """返回适合模型上下文的有界摘要。"""

    def long_term(self) -> list[LongTermMemoryRecord]:
        """返回已经完成隔离和权威过滤的长期记忆。"""

__all__ = ["ContextMemory"]

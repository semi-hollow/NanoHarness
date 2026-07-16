"""长期记忆持久化端口。"""

from __future__ import annotations

from typing import Protocol

from agent_forge.context.domain import LongTermMemoryRecord


class LongTermMemoryRepository(Protocol):
    """Application 使用的最小长期记忆存储契约。"""

    def save(self, record: LongTermMemoryRecord) -> None:
        """原子保存一条记录。"""

    def get(self, memory_id: str) -> LongTermMemoryRecord | None:
        """按稳定 ID 读取记录。"""

    def list_records(self, namespace: str | None = None) -> list[LongTermMemoryRecord]:
        """列出全部记录，或只列出一个隔离命名空间。"""


class LongTermMemoryRecallPort(Protocol):
    """Runtime 只需要长期记忆的只读召回能力。"""

    def recall(
        self,
        query: str,
        *,
        namespace: str,
        agent_name: str,
        limit: int = 6,
    ) -> list[LongTermMemoryRecord]:
        """返回已经过状态和隔离过滤的记录。"""

"""长期记忆持久化端口。"""

from __future__ import annotations

from typing import Protocol

from agent_forge.memory.domain import LongTermMemoryRecord


class LongTermMemoryRepository(Protocol):
    """Application 使用的最小长期记忆存储契约。"""

    def save(self, record: LongTermMemoryRecord) -> None:
        """原子保存一条记录。"""

    def get(self, memory_id: str) -> LongTermMemoryRecord | None:
        """按稳定 ID 读取记录。"""

    def list_records(self, namespace: str | None = None) -> list[LongTermMemoryRecord]:
        """列出全部记录，或只列出一个隔离命名空间。"""

    def delete(self, memory_id: str) -> None:
        """按稳定 ID 物理删除一条记忆。"""


class LongTermMemoryRecallPort(Protocol):
    """Runtime 只需要长期记忆的有界只读查询能力。"""

    def recall(
        self,
        *,
        namespace: str,
        query: str = "",
        max_chars: int = 2_000,
    ) -> list[LongTermMemoryRecord]:
        """按 Context 字符预算返回 Run 开始时固定的完整记录快照。"""

    def management_candidates(
        self,
        *,
        namespace: str,
        query: str,
        max_chars: int = 2_000,
    ) -> list[LongTermMemoryRecord]:
        """按当前 human message 返回仅供显式 remember 合并的有界候选。"""

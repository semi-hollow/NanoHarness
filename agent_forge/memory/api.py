"""CLI、Operator Console 等外围入口使用的 Long-Term Memory 公共 API。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_forge.memory.adapters import JsonLongTermMemoryRepository
from agent_forge.memory.application import LongTermMemoryService
from agent_forge.memory.domain import LongTermMemoryRecord


@dataclass(frozen=True, kw_only=True)
class RememberMemoryRequest:
    """用户从 CLI 或 Operator Console 显式提交的记忆。"""

    memory_root: str
    workspace: str
    key: str
    content: str
    scope: str = "project"


# 主要入口：显式新增或更新一条长期记忆。
def remember_memory(request: RememberMemoryRequest) -> LongTermMemoryRecord:
    """同作用域、同 key 保留 ID 并递增 revision。"""

    return _service(request.memory_root).remember(
        project_namespace=resolve_project_namespace(request.workspace),
        key=request.key,
        content=request.content,
        scope=request.scope,
    )


# 主要入口：显式删除一条长期记忆。
def forget_memory(memory_root: str, memory_id: str) -> LongTermMemoryRecord:
    """返回被删除的记录，便于 UI 给出明确反馈。"""

    return _service(memory_root).forget(memory_id)


# 主要入口：列出用户全局和当前项目的记忆。
def list_memories(
    memory_root: str,
    workspace: str,
    *,
    scope: str | None = None,
) -> list[LongTermMemoryRecord]:
    """列出可管理原始记录，不隐藏同 key 的作用域覆盖关系。"""

    return _service(memory_root).list_for_project(
        project_namespace=resolve_project_namespace(workspace),
        scope=scope,
    )


def resolve_project_namespace(workspace: str) -> str:
    """将项目路径归一化为 project scope 的隔离键。"""

    return str(Path(workspace).expanduser().resolve())


def _service(memory_root: str) -> LongTermMemoryService:
    return LongTermMemoryService(JsonLongTermMemoryRepository(memory_root))


__all__ = [
    "forget_memory",
    "list_memories",
    "RememberMemoryRequest",
    "remember_memory",
    "resolve_project_namespace",
]

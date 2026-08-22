"""任务会话目录的持久化 Port，相当于 Java Repository 接口。"""

from __future__ import annotations

from typing import Protocol

from apps.operator_console.domain import TaskSession


class TaskSessionCatalogPort(Protocol):
    """Application 层依赖的会话仓储契约，不规定 JSON 或数据库。"""

    def save(self, session: TaskSession) -> None: ...

    def get(self, session_id: str) -> TaskSession | None: ...

    def list_all(self) -> list[TaskSession]: ...

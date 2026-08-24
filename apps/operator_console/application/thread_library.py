"""Operator Console 的 Conversation Thread 导航服务。"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import replace
from pathlib import Path

from agent_forge.runtime.domain.thread import ConversationThread
from agent_forge.runtime.ports.thread import ConversationThreadRepository


class ConversationThreadLibrary:
    """只管理 canonical Thread 的标题、置顶与归档，不复制 Run/Conversation。"""

    def __init__(self, repository: ConversationThreadRepository) -> None:
        self.repository = repository

    def create(
        self,
        *,
        task: str,
        workspace: str | Path,
        title: str = "",
    ) -> ConversationThread:
        now = time.time()
        return self.repository.create(
            ConversationThread(
                thread_id=f"thread-{uuid.uuid4().hex[:12]}",
                title=self._normalize_title(title or task),
                initial_task=task.strip(),
                workspace=str(Path(workspace).expanduser().resolve()),
                created_at=now,
                updated_at=now,
            )
        )

    def list_active(self) -> list[ConversationThread]:
        return sorted(
            (thread for thread in self.repository.list_all() if not thread.archived),
            key=lambda thread: (not thread.pinned, -thread.updated_at),
        )

    def require(self, thread_id: str) -> ConversationThread:
        thread = self.repository.get(thread_id)
        if thread is None:
            raise KeyError(f"conversation thread not found: {thread_id}")
        return thread

    def rename(self, thread_id: str, title: str) -> ConversationThread:
        return self.repository.save_metadata(
            replace(
                self.require(thread_id),
                title=self._normalize_title(title),
                updated_at=time.time(),
            )
        )

    def set_archived(
        self,
        thread_id: str,
        archived: bool = True,
    ) -> ConversationThread:
        return self.repository.save_metadata(
            replace(
                self.require(thread_id),
                archived=archived,
                updated_at=time.time(),
            )
        )

    def toggle_pinned(self, thread_id: str) -> ConversationThread:
        thread = self.require(thread_id)
        return self.repository.save_metadata(
            replace(
                thread,
                pinned=not thread.pinned,
                updated_at=time.time(),
            )
        )

    @staticmethod
    def _normalize_title(value: str) -> str:
        title = re.sub(r"\s+", " ", value).strip()
        if not title:
            return "未命名会话"
        return title if len(title) <= 48 else f"{title[:47]}…"


__all__ = ["ConversationThreadLibrary"]

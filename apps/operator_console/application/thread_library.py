"""Operator Console 的 Conversation Thread 导航 Application。

系统角色：把 UI 的创建、查找、重命名、归档和置顶动作翻译成 Repository mutation；
不读取 Runtime checkpoint，也不复制 Conversation/Run truth。
输入：用户导航操作；输出：更新后的 canonical ``ConversationThread``。
相邻边界：UI 只调用本类；``ConversationThreadRepository`` 拥有真实持久化。

折叠导航：1 创建与查询；2 纯导航 metadata；3 标题规范化。
"""

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

    # region 1. 创建与查询：建立 canonical Thread，并按置顶/更新时间投影列表
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
    # endregion 1. 创建与查询结束

    # region 2. 导航 metadata：不允许 UI 改写 Turn、Run 或 Conversation
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
    # endregion 2. 导航 metadata 结束

    # region 3. 标题规范化：只控制展示长度，不改变 root task
    @staticmethod
    def _normalize_title(value: str) -> str:
        title = re.sub(r"\s+", " ", value).strip()
        if not title:
            return "未命名会话"
        return title if len(title) <= 48 else f"{title[:47]}…"
    # endregion 3. 标题规范化结束


__all__ = ["ConversationThreadLibrary"]

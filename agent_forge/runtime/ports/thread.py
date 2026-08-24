"""Conversation Thread 的持久化 Port。"""

from __future__ import annotations

from typing import Protocol

from agent_forge.runtime.domain.thread import (
    ConversationItem,
    ConversationItemDraft,
    ConversationThread,
    ThreadContextState,
    ThreadRun,
    Turn,
    TurnContextSnapshot,
)


class ConversationThreadRepository(Protocol):
    """Thread、authoritative conversation 与 context projection 的唯一仓储边界。"""

    def create(self, thread: ConversationThread) -> ConversationThread:
        """创建新 Thread；相同 ID 已存在时 fail closed。"""

    def get(self, thread_id: str) -> ConversationThread | None:
        """读取并校验 Thread metadata 与 journal tail。"""

    def list_all(self) -> list[ConversationThread]:
        """返回全部可加载 Thread。"""

    def save_metadata(self, thread: ConversationThread) -> ConversationThread:
        """保存 title/pinned/archived 等导航字段，不改写 Conversation。"""

    def start_turn(
        self,
        thread_id: str,
        turn: Turn,
        input_item: ConversationItemDraft,
        initial_run: ThreadRun,
        *,
        snapshot: TurnContextSnapshot | None = None,
        expected_context_revision: int | None = None,
    ) -> tuple[ConversationThread, ConversationItem]:
        """同锁冻结可选 snapshot，再创建 active Turn + initial Run 导航。"""

    def record_run(
        self,
        thread_id: str,
        turn_id: str,
        run: ThreadRun,
    ) -> ConversationThread:
        """幂等 upsert 一次 Run，并据其状态刷新 active Turn。"""

    def claim_resume_run(
        self,
        thread_id: str,
        turn_id: str,
        *,
        expected_current_run_id: str,
        run: ThreadRun,
    ) -> ConversationThread:
        """以 current Run 为 CAS 前提，原子 claim 同一 Turn 的新 resume Run。"""

    def prepare_turn_terminal(
        self,
        thread_id: str,
        turn_id: str,
        *,
        run_id: str,
        status: str,
    ) -> ConversationThread:
        """在 terminal checkpoint 之前持久化当前 Run 的终态提交意图。"""

    def finish_turn(
        self,
        thread_id: str,
        turn_id: str,
        status: str,
        *,
        run_id: str,
    ) -> ConversationThread:
        """在 final/checkpoint 已 durable 后收口一个 terminal Turn。"""

    def append(
        self,
        thread_id: str,
        item: ConversationItemDraft,
    ) -> ConversationItem:
        """分配 sequence/time/hash 后 durable append；逻辑幂等冲突 fail closed。"""

    def get_item(self, thread_id: str, item_id: str) -> ConversationItem | None:
        """按稳定 item_id 读取权威消息。"""

    def list_items(
        self,
        thread_id: str,
        *,
        after_sequence: int = 0,
        turn_id: str | None = None,
        limit: int = 200,
    ) -> list[ConversationItem]:
        """按 sequence 返回有界 Conversation slice。"""

    def list_recent_items(
        self,
        thread_id: str,
        *,
        turn_id: str | None = None,
        limit: int = 200,
    ) -> list[ConversationItem]:
        """单次 scan 返回最新有界 tail，不要求 Session 全量加载历史。"""

    def load_context_state(self, thread_id: str) -> ThreadContextState | None:
        """读取 digest、covered sequence 与 Turn snapshots。"""

    def save_context_state(
        self,
        state: ThreadContextState,
        *,
        expected_revision: int,
    ) -> ThreadContextState:
        """CAS 更新 context_state.json 并分配下一 revision。"""

    def load_turn_snapshot(
        self,
        thread_id: str,
        turn_id: str,
    ) -> TurnContextSnapshot | None:
        """读取同一 Turn 跨 Run 复用的稳定输入快照。"""

    def save_turn_snapshot(
        self,
        thread_id: str,
        snapshot: TurnContextSnapshot,
        *,
        expected_revision: int,
    ) -> ThreadContextState:
        """CAS 写入一个 Turn snapshot，并返回新的整体 context revision。"""


__all__ = ["ConversationThreadRepository"]

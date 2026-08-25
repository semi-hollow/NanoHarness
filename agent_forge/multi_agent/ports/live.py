"""LIVE Worker 绑定后的最小 coordination Port。"""

from __future__ import annotations

from typing import Protocol

from ..domain.live_handoff import LiveHandoffEvent


class LiveWorkerContextPort(Protocol):
    """只允许一个 Worker Attempt 发布和消费有界 LIVE 语义事实。"""

    task_id: str
    worker_attempt_id: int

    def publish(
        self,
        *,
        event_type: str,
        target_task_id: str,
        semantic_key: str,
        version: int,
        summary: str,
        evidence: list[str],
        caused_by_event_id: str = "",
    ) -> LiveHandoffEvent:
        """由 Runtime 注入 publisher/frozen-plan/attempt 后发布事实。"""

    def drain_mailbox(self, *, boundary: str) -> list[LiveHandoffEvent]:
        """在真实 AgentLoop 安全边界消费当前 Attempt 的事实。"""

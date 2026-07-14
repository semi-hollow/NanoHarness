"""运行事件输出端口。"""

from __future__ import annotations

from typing import Any, Protocol

from agent_forge.observability.domain.event import TraceEventType
from agent_forge.runtime.domain.task import TaskCheckpoint


class EventSink(Protocol):
    """Application 记录事实所需的接口，不包含具体 JSON 格式。"""

    run_id: str

    def set_run_context(
        self,
        task: str = "",
        stop_reason: str = "",
        final_answer: str = "",
    ) -> None:
        """更新一次运行的顶层事实。"""

    def add(
        self,
        step: int,
        agent_name: str,
        event_type: TraceEventType,
        success: bool = True,
        error: str = "",
        **data: Any,
    ) -> None:
        """追加一个结构化运行事件。"""

    def record_task_state_checkpoint(
        self,
        *,
        step: int,
        agent_name: str,
        checkpoint: TaskCheckpoint,
    ) -> None:
        """记录类型化 checkpoint。"""

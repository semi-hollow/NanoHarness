from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from agent_forge.observability.domain.event import TraceEvent, TraceEventType, TraceRecord
from agent_forge.observability.domain.metrics import summarize
from agent_forge.observability.presentation.trace_summary import render_trace_summary
from agent_forge.runtime.ports.events import EventSink

if TYPE_CHECKING:
    from agent_forge.runtime.domain.task import TaskCheckpoint


class JsonTraceRecorder(EventSink):
    """把 Runtime 事件追加为统一信封，并在终态发布 ``trace.json``。

    可类比 Java 中的结构化审计日志 Adapter：业务流程决定“发生了什么”，本类只负责补齐
    run_id、时间间隔等公共字段并持久化，不参与权限、恢复或评测决策。
    """

    def __init__(self, path: str, verbose: bool = False, write_summary_file: bool = False) -> None:
        self.path = path
        self.verbose = verbose
        self.write_summary_file = write_summary_file
        self.run_id = str(uuid.uuid4())
        self.events: list[TraceRecord] = []
        self.started_at = time.time()
        self._last_event_at = self.started_at
        self.task = ""
        self.stop_reason = ""
        self.final_answer = ""

    def set_run_context(self, task: str = "", stop_reason: str = "", final_answer: str = "") -> None:
        """补充整次运行的任务和终态信息；这些字段位于事件流之外。"""
        if task:
            self.task = task
        if stop_reason:
            self.stop_reason = stop_reason
        if final_answer:
            self.final_answer = final_answer

    def add(
        self,
        step: int,
        agent_name: str,
        event_type: TraceEventType,
        success: bool = True,
        error: str = "",
        **data: Any,
    ) -> None:
        """兼容现有调用方的通用记录入口；新增核心事件优先使用类型化方法。"""
        self._append(step, agent_name, event_type, success=success, error=error, data=data)

    def record_task_state_checkpoint(
        self,
        *,
        step: int,
        agent_name: str,
        checkpoint: "TaskCheckpoint",
    ) -> None:
        """记录一份可恢复状态快照，供 Workbench 对比相邻状态转换。"""
        self._append(
            step,
            agent_name,
            "task_state_checkpoint",
            data={"task_state": checkpoint.to_dict()},
        )

    def record_event(
        self,
        *,
        step: int,
        agent_name: str,
        event_type: TraceEventType,
        success: bool = True,
        error: str = "",
        data: Mapping[str, Any] | None = None,
    ) -> None:
        """实现 EventSink 的类型化事件入口，保留调用方已经定义好的业务字段。"""
        self._append(
            step,
            agent_name,
            event_type,
            success=success,
            error=error,
            data=dict(data or {}),
        )

    def _append(
        self,
        step: int,
        agent_name: str,
        event_type: TraceEventType,
        *,
        success: bool = True,
        error: str = "",
        data: Mapping[str, Any] | None = None,
    ) -> None:
        """统一补齐事件信封、计算距上次事件的耗时并追加到内存事实流。"""
        now = time.time()
        event = TraceEvent(
            run_id=self.run_id,
            step=step,
            agent_name=agent_name,
            event_type=event_type,
            duration_ms=int((now - self._last_event_at) * 1000),
            success=success,
            error=error,
            data=data or {},
        ).to_dict()
        self._last_event_at = now
        self.events.append(event)
        if self.verbose:
            print(f"[trace] step={step} agent={agent_name} event={event_type} success={success}")

    def write(self) -> None:
        """将当前事实流和 run context 发布为 ``trace.json``。"""
        trace = {
            "run_id": self.run_id,
            "task": self.task,
            "start_time": self.started_at,
            "end_time": time.time(),
            "stop_reason": self.stop_reason,
            "final_answer": self.final_answer,
            "events": self.events,
            "metrics": summarize(self.events),
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(trace, f, ensure_ascii=False, indent=2)
        if self.write_summary_file:
            summary_path = Path(self.path).with_name("summary.md")
            summary_path.write_text(render_trace_summary(trace), encoding="utf-8")

    def publish(self) -> None:
        """实现 EventSink 的终态发布端口；``write`` 保留给现有调用方。"""

        self.write()

TraceRecorder = JsonTraceRecorder

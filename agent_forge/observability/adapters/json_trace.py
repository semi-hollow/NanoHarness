from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from agent_forge.observability.domain.event import TraceEvent, TraceEventType, TraceRecord
from agent_forge.observability.domain.metrics import summarize
from agent_forge.observability.presentation.trace_summary import render_trace_summary

if TYPE_CHECKING:
    from agent_forge.runtime.domain.task import TaskCheckpoint


class JsonTraceRecorder:

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

        self._append(step, agent_name, event_type, success=success, error=error, data=data)

    # 运行时端口：下方定义连接用例与外部实现。
    def record_task_state_checkpoint(
        self,
        *,
        step: int,
        agent_name: str,
        checkpoint: "TaskCheckpoint",
    ) -> None:

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

    # 运行时端口：下方定义连接用例与外部实现。
    def write(self) -> None:

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

TraceRecorder = JsonTraceRecorder

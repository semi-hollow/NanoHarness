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
    """Collect and write the auditable event stream for one run.

    Trace is the main explainability artifact. It records not only final output,
    but context selection, model calls, permission decisions, tool observations,
    recovery decisions, and metrics.

    Why it exists:
        Without trace, an agent run is just a final sentence and maybe a diff.
        Trace gives you the evidence needed to answer why a file was selected,
        why a tool was allowed, where the model failed, and why the loop stopped.

    Method map:
        ``set_run_context`` updates top-level task/stop/final metadata.
        ``add`` appends one structured event.
        ``write`` persists the final JSON plus optional summary.
    """

    def __init__(self, path: str, verbose: bool = False, write_summary_file: bool = False) -> None:
        """Initialize run metadata and destination trace path.

        Terminal trace spam is useful while building the runtime, but it is
        noise for study. The JSON trace remains complete; console breadcrumbs
        and sibling summary files are opt-in.
        """

        # Destination JSON path. Session mode usually points this into a run dir.
        self.path = path

        # Verbose prints breadcrumbs while developing; default stays quiet for
        # study so the user reads trace/report intentionally.
        self.verbose = verbose
        self.write_summary_file = write_summary_file

        # One id shared by every event in this trace.
        self.run_id = str(uuid.uuid4())

        # Append-only in-memory event list; write() persists it at the end.
        self.events: list[TraceRecord] = []
        self.started_at = time.time()

        # Used to compute per-event duration deltas.
        self._last_event_at = self.started_at

        # Top-level run context, updated by AgentLoop/Supervisor.
        self.task = ""
        self.stop_reason = ""
        self.final_answer = ""

    def set_run_context(self, task: str = "", stop_reason: str = "", final_answer: str = "") -> None:
        """Update top-level run fields without touching existing event history."""

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
        """Compatibility path for low-frequency events.

        Core runtime events should use a named ``record_*`` method so a reader
        can jump directly from the call site to its typed payload contract.
        ``add`` remains for extension events while callers migrate.
        """

        self._append(step, agent_name, event_type, success=success, error=error, data=data)

    # RUNTIME PORT: keep a typed checkpoint until the trace serialization boundary.
    def record_task_state_checkpoint(
        self,
        *,
        step: int,
        agent_name: str,
        checkpoint: "TaskCheckpoint",
    ) -> None:
        """Record a resumable ``TaskCheckpoint`` without erasing its type early."""

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
        """Record one extension event from an explicit payload mapping."""

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
        """Create the validated event envelope and append its JSON form."""

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

    # RUNTIME PORT: CLI and benchmark runners finalize the trace artifact here.
    def write(self) -> None:
        """Persist the complete event stream, metrics, and optional summary.

        Execution paths call this after ``AgentLoop`` or a coordinator returns.
        Readers interested in event creation should start at named ``record_*``
        methods; ``_append`` is only envelope construction.
        """

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

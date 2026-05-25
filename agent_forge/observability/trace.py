import json
import time
import uuid

from .metrics import summarize
from .summary import write_summary


class TraceRecorder:
    """Collect and write the auditable event stream for one run.

    Trace is the main explainability artifact. It records not only final output,
    but context selection, model calls, permission decisions, tool observations,
    recovery decisions, and metrics.
    """

    def __init__(self, path: str, verbose: bool = False, write_summary_file: bool = False):
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
        self.events: list[dict] = []
        self.started_at = time.time()

        # Used to compute per-event duration deltas.
        self._last_event_at = self.started_at

        # Top-level run context, updated by AgentLoop/Supervisor.
        self.task = ""
        self.stop_reason = ""
        self.final_answer = ""

    def set_run_context(self, task: str = "", stop_reason: str = "", final_answer: str = ""):
        """Update top-level run fields without touching existing event history."""

        if task:
            self.task = task
        if stop_reason:
            self.stop_reason = stop_reason
        if final_answer:
            self.final_answer = final_answer

    def add(self, step: int, agent_name: str, event_type: str, success: bool = True, error: str = "", **kwargs):
        """Append one timestamped event and print a short terminal breadcrumb.

        Extra keyword fields are intentionally open-ended so each subsystem can
        write domain evidence without changing a global event schema.
        """

        now = time.time()
        event = {
            "run_id": self.run_id,
            "step": step,
            "agent_name": agent_name,
            "event_type": event_type,
            "duration_ms": int((now - self._last_event_at) * 1000),
            "success": success,
            "error": error,
            **kwargs,
        }
        self._last_event_at = now
        self.events.append(event)
        if self.verbose:
            print(f"[trace] step={step} agent={agent_name} event={event_type} success={success}")

    def write(self):
        """Write JSON trace plus the human-readable summary file."""

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
            write_summary(self.path, trace)

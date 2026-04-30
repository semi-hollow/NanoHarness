import json
import time
import uuid

from .metrics import summarize


class TraceRecorder:
    def __init__(self, path: str):
        self.path = path
        self.run_id = str(uuid.uuid4())
        self.events: list[dict] = []
        self.started_at = time.time()
        self._last_event_at = self.started_at

    def add(self, step: int, agent_name: str, event_type: str, success: bool = True, error: str = "", **kwargs):
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
        print(f"[trace] step={step} agent={agent_name} event={event_type} success={success}")

    def write(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"run_id": self.run_id, "events": self.events, "metrics": summarize(self.events)}, f, ensure_ascii=False, indent=2)

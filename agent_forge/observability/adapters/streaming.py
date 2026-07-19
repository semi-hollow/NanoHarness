"""把内部 EventSink 双写为脱敏实时事件流。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from agent_forge.observability.domain.event import TraceEventType
from agent_forge.observability.domain.live_event import RuntimeEvent
from agent_forge.observability.ports.events import RuntimeEventListener
from agent_forge.runtime.domain.task import TaskCheckpoint
from agent_forge.runtime.ports import EventSink


_EVENT_NAMES: dict[str, str] = {
    "turn_started": "turn.started",
    "context_assembly": "context.completed",
    "context_window": "context.window",
    "model_started": "model.started",
    "llm_call": "model.completed",
    "action": "tool.proposed",
    "tool_execution_started": "tool.started",
    "tool_call": "tool.recorded",
    "tool_observation": "tool.completed",
    "human_input_requested": "human.required",
    "human_approval": "approval.updated",
    "run_control": "run.control",
    "run_completed": "run.completed",
    "skill_selection": "skill.selected",
}
_SENSITIVE_KEYS = {
    "answer",
    "arguments",
    "command",
    "content",
    "final_answer",
    "last_observation",
    "llm_request_summary",
    "llm_response_summary",
    "message",
    "observation",
    "prompt",
    "question",
    "reason",
    "request",
    "task",
    "tool_arguments",
}


@dataclass(frozen=True)
class EventStreamPolicy:
    """控制实时事件是否包含内容字段，以及单个文本字段的上限。"""

    include_sensitive_data: bool = False
    max_text_chars: int = 500
    fail_on_listener_error: bool = False


class StreamingEventSink:
    """保留原始 EventSink，同时按提交顺序通知实时 listener。"""

    def __init__(
        self,
        delegate: EventSink,
        listeners: Sequence[RuntimeEventListener],
        policy: EventStreamPolicy | None = None,
    ) -> None:
        self.delegate = delegate
        self.listeners = tuple(listeners)
        self.policy = policy or EventStreamPolicy()
        self.run_id = delegate.run_id
        self.sequence = 0
        self.listener_errors: list[str] = []
        self._started = False

    def set_run_context(
        self,
        task: str = "",
        stop_reason: str = "",
        final_answer: str = "",
    ) -> None:
        self.delegate.set_run_context(task, stop_reason, final_answer)
        if task and not self._started:
            self._started = True
            payload: dict[str, object] = {
                "task_chars": len(task),
                "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
            }
            if self.policy.include_sensitive_data:
                payload["task"] = task[: self.policy.max_text_chars]
            self._emit("run.started", 0, "Runtime", True, payload)

    # 主要入口：保留内部事实，并同步投影一个脱敏有序 RuntimeEvent。
    def add(
        self,
        step: int,
        agent_name: str,
        event_type: TraceEventType,
        success: bool = True,
        error: str = "",
        **data: Any,
    ) -> None:
        """先写内部事实事件，再按同一顺序发布脱敏的实时事件。"""
        self.delegate.add(
            step,
            agent_name,
            event_type,
            success=success,
            error=error,
            **data,
        )
        payload = self._sanitize(data)
        if error:
            payload["error_chars"] = len(error)
            payload["error_sha256"] = hashlib.sha256(error.encode("utf-8")).hexdigest()
            if self.policy.include_sensitive_data:
                payload["error"] = error[: self.policy.max_text_chars]
        name = _EVENT_NAMES.get(str(event_type), f"runtime.{event_type}")
        self._emit(name, step, agent_name, success, payload)

    def record_task_state_checkpoint(
        self,
        *,
        step: int,
        agent_name: str,
        checkpoint: TaskCheckpoint,
    ) -> None:
        self.delegate.record_task_state_checkpoint(
            step=step,
            agent_name=agent_name,
            checkpoint=checkpoint,
        )
        self._emit(
            "checkpoint.saved",
            step,
            agent_name,
            True,
            {
                "status": checkpoint.status,
                "stop_reason": checkpoint.stop_reason,
                "messages_count": checkpoint.messages_count,
                "observations_count": checkpoint.observations_count,
            },
        )

    def publish(self) -> None:
        self.delegate.publish()
        self._emit("run.published", 0, "Runtime", True, {})

    def _emit(
        self,
        name: str,
        step: int,
        agent_name: str,
        success: bool,
        payload: Mapping[str, object],
    ) -> None:
        self.sequence += 1
        event = RuntimeEvent(
            name=name,
            run_id=self.run_id,
            sequence=self.sequence,
            step=step,
            agent_name=agent_name,
            success=success,
            payload=payload,
        )
        for listener in self.listeners:
            try:
                listener.on_event(event)
            except Exception as exc:
                self.listener_errors.append(f"{type(exc).__name__}: {exc}")
                if self.policy.fail_on_listener_error:
                    raise

    def _sanitize(self, value: Mapping[str, Any]) -> dict[str, object]:
        return {
            str(key): self._safe_value(child, key=str(key))
            for key, child in value.items()
            if self.policy.include_sensitive_data or str(key).lower() not in _SENSITIVE_KEYS
        }

    def _safe_value(self, value: Any, *, key: str = "") -> object:
        if not self.policy.include_sensitive_data and key.lower() in _SENSITIVE_KEYS:
            return "[redacted]"
        if isinstance(value, str):
            return value[: self.policy.max_text_chars]
        if isinstance(value, (bool, int, float)) or value is None:
            return value
        if isinstance(value, Mapping):
            return {
                str(child_key): self._safe_value(child, key=str(child_key))
                for child_key, child in value.items()
                if self.policy.include_sensitive_data
                or str(child_key).lower() not in _SENSITIVE_KEYS
            }
        if isinstance(value, (list, tuple)):
            return [self._safe_value(item) for item in value[:50]]
        return str(value)[: self.policy.max_text_chars]

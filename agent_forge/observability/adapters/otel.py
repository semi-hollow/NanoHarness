"""将脱敏 RuntimeEvent 可选投影到 OpenTelemetry Span。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent_forge.observability.domain.live_event import RuntimeEvent


class SpanLike(Protocol):
    def set_attribute(self, key: str, value: object) -> None: ...
    def add_event(self, name: str, attributes: dict[str, object] | None = None) -> None: ...
    def end(self) -> None: ...


class TracerLike(Protocol):
    def start_span(
        self,
        name: str,
        *,
        context: object | None = None,
        attributes: dict[str, object] | None = None,
    ) -> SpanLike: ...


@dataclass(frozen=True)
class OpenTelemetryPolicy:
    """默认只导出 envelope；开启 payload 也只使用已脱敏的 stream event。"""

    include_payload: bool = False
    max_attribute_chars: int = 300


class OpenTelemetryEventListener:
    """不改变内部 trace 的可选 OTEL 双写 listener。

    ``model.started/tool.started`` 打开 span，对应 completed 事件关闭 span；只有
    completed 而没有 started 时，才降级为零时长 span。内部 JSON 始终是事实源。
    """

    def __init__(
        self,
        tracer: TracerLike,
        policy: OpenTelemetryPolicy | None = None,
    ) -> None:
        self.tracer = tracer
        self.policy = policy or OpenTelemetryPolicy()
        self._roots: dict[str, SpanLike] = {}
        self._active: dict[tuple[str, str, str], SpanLike] = {}

    # 主要入口：把 RuntimeEvent 映射为 run root span、子 span 或 root event。
    def on_event(self, event: RuntimeEvent) -> None:
        """run 完成时关闭 root；LLM、Tool、Context 等事实投影为子 span。"""

        if event.name == "run.published":
            root = self._roots.pop(event.run_id, None)
            if root is not None:
                self._end_orphan_spans(event.run_id)
                root.add_event(event.name, self._attributes(event))
                root.end()
            return
        root = self._roots.get(event.run_id)
        if event.name == "run.started":
            if root is None:
                self._roots[event.run_id] = self.tracer.start_span(
                    "invoke_agent",
                    attributes=self._attributes(event),
                )
            return
        if root is None:
            root = self.tracer.start_span(
                "invoke_agent",
                attributes={"gen_ai.run.id": event.run_id},
            )
            self._roots[event.run_id] = root
        if event.name == "run.completed":
            self._end_orphan_spans(event.run_id)
            root.add_event(event.name, self._attributes(event))
            root.end()
            self._roots.pop(event.run_id, None)
            return
        category = _span_category(event.name)
        if category is not None and event.name.endswith(".started"):
            key = _span_key(event, category)
            prior = self._active.pop(key, None)
            if prior is not None:
                prior.end()
            self._active[key] = self.tracer.start_span(
                _span_name(event.name),
                context=_span_context(root),
                attributes=self._attributes(event),
            )
            return
        if category is not None and event.name.endswith(".completed"):
            key = _span_key(event, category)
            span = self._active.pop(key, None)
            if span is None:
                span = self.tracer.start_span(
                    _span_name(event.name),
                    context=_span_context(root),
                    attributes=self._attributes(event),
                )
            else:
                for name, value in self._attributes(event).items():
                    span.set_attribute(name, value)
            span.add_event(event.name)
            span.end()
            return
        if not (
            event.name.startswith("context.")
            or event.name.startswith("evaluation.")
        ):
            root.add_event(event.name, self._attributes(event))
            return
        span = self.tracer.start_span(
            _span_name(event.name),
            context=_span_context(root),
            attributes=self._attributes(event),
        )
        span.add_event(event.name)
        span.end()

    def _end_orphan_spans(self, run_id: str) -> None:
        """终态兜底关闭缺少 completed 事件的子 span，避免 exporter 泄漏。"""

        keys = [key for key in self._active if key[0] == run_id]
        for key in keys:
            self._active.pop(key).end()

    def _attributes(self, event: RuntimeEvent) -> dict[str, object]:
        attributes: dict[str, object] = {
            "gen_ai.run.id": event.run_id,
            "gen_ai.event.name": event.name,
            "gen_ai.agent.name": event.agent_name,
            "gen_ai.step": event.step,
            "gen_ai.success": event.success,
        }
        if self.policy.include_payload:
            for key, value in event.payload.items():
                if isinstance(value, (str, bool, int, float)):
                    attributes[f"gen_ai.payload.{key}"] = (
                        value[: self.policy.max_attribute_chars]
                        if isinstance(value, str)
                        else value
                    )
        return attributes


def _span_name(event_name: str) -> str:
    if event_name.startswith("model."):
        return "chat"
    if event_name.startswith("tool."):
        return "execute_tool"
    if event_name.startswith("context."):
        return "retrieval"
    if event_name.startswith("evaluation."):
        return "evaluation"
    return "runtime.event"


def _span_category(event_name: str) -> str | None:
    if event_name.startswith("model."):
        return "model"
    if event_name.startswith("tool.") and event_name != "tool.proposed":
        return "tool"
    return None


def _span_key(event: RuntimeEvent, category: str) -> tuple[str, str, str]:
    identity = str(event.payload.get("tool_call_id") or event.step)
    return event.run_id, category, identity


def _span_context(root: SpanLike) -> object | None:
    try:
        from opentelemetry import trace

        return trace.set_span_in_context(root)
    except (ImportError, TypeError, AttributeError):
        return None

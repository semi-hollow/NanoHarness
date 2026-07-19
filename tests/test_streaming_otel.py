import tempfile
import unittest
from pathlib import Path

from agent_forge.observability.adapters import (
    EventStreamPolicy,
    OpenTelemetryEventListener,
    StreamingEventSink,
)
from agent_forge.observability.adapters.json_trace import JsonTraceRecorder
from agent_forge.observability.domain import RuntimeEvent


class Collector:
    def __init__(self):
        self.events = []

    def on_event(self, event):
        self.events.append(event)


class FakeSpan:
    def __init__(self, name, attributes):
        self.name = name
        self.attributes = dict(attributes or {})
        self.events = []
        self.ended = False

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def add_event(self, name, attributes=None):
        self.events.append((name, dict(attributes or {})))

    def end(self):
        self.ended = True


class FakeTracer:
    def __init__(self):
        self.spans = []

    def start_span(self, name, *, context=None, attributes=None):
        span = FakeSpan(name, attributes)
        self.spans.append(span)
        return span


class StreamingOtelTest(unittest.TestCase):
    def test_streaming_projection_redacts_tool_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            collector = Collector()
            sink = StreamingEventSink(
                JsonTraceRecorder(str(Path(tmp) / "trace.json")),
                [collector],
                EventStreamPolicy(),
            )
            sink.set_run_context(task="private task")
            sink.add(
                1,
                "Agent",
                "action",
                tool_call="run_command",
                tool_arguments={"command": "echo secret-value"},
            )
            sink.add(
                1,
                "Agent",
                "tool_execution_started",
                tool_call="run_command",
                tool_call_id="call-1",
            )
            sink.add(
                1,
                "Agent",
                "tool_observation",
                tool_call="run_command",
                tool_call_id="call-1",
                observation="secret-value",
            )
            sink.publish()

            proposed = next(event for event in collector.events if event.name == "tool.proposed")
            self.assertEqual(proposed.payload["tool_call"], "run_command")
            self.assertNotIn("tool_arguments", proposed.payload)
            self.assertNotIn("secret-value", str(proposed.to_dict()))
            self.assertIn("tool.started", [event.name for event in collector.events])
            completed = next(
                event for event in collector.events if event.name == "tool.completed"
            )
            self.assertNotIn("observation", completed.payload)
            self.assertEqual(
                [event.sequence for event in collector.events],
                list(range(1, len(collector.events) + 1)),
            )

    def test_otel_listener_creates_root_and_semantic_child_spans(self):
        tracer = FakeTracer()
        listener = OpenTelemetryEventListener(tracer)
        listener.on_event(RuntimeEvent("run.started", "run-1", 1, 0, "Agent"))
        listener.on_event(RuntimeEvent("model.started", "run-1", 2, 1, "Agent"))
        listener.on_event(RuntimeEvent("model.completed", "run-1", 3, 1, "Agent"))
        listener.on_event(
            RuntimeEvent(
                "tool.started",
                "run-1",
                4,
                1,
                "Agent",
                payload={"tool_call_id": "call-1"},
            )
        )
        listener.on_event(
            RuntimeEvent(
                "tool.completed",
                "run-1",
                5,
                1,
                "Agent",
                payload={"tool_call_id": "call-1"},
            )
        )
        listener.on_event(
            RuntimeEvent(
                "run.completed",
                "run-1",
                6,
                1,
                "Agent",
                payload={"status": "completed"},
            )
        )

        self.assertEqual(
            [span.name for span in tracer.spans],
            ["invoke_agent", "chat", "execute_tool"],
        )
        self.assertTrue(all(span.ended for span in tracer.spans))
        self.assertTrue(tracer.spans[0].ended)
        self.assertNotIn("gen_ai.payload.status", tracer.spans[0].attributes)

    def test_publish_closes_spans_after_an_unexpected_runtime_exit(self):
        tracer = FakeTracer()
        listener = OpenTelemetryEventListener(tracer)
        listener.on_event(RuntimeEvent("run.started", "run-1", 1, 0, "Agent"))
        listener.on_event(RuntimeEvent("model.started", "run-1", 2, 1, "Agent"))

        listener.on_event(RuntimeEvent("run.published", "run-1", 3, 1, "Agent"))

        self.assertEqual([span.name for span in tracer.spans], ["invoke_agent", "chat"])
        self.assertTrue(all(span.ended for span in tracer.spans))


if __name__ == "__main__":
    unittest.main()

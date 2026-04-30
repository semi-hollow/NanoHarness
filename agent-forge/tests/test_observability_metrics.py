import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.observability.metrics import summarize
from agent_forge.observability.trace import TraceRecorder


class TestObservabilityMetrics(unittest.TestCase):
    def test_summarize_metrics(self):
        metrics = summarize([
            {"event_type": "tool_call", "duration_ms": 3},
            {"event_type": "tool_observation", "success": False, "duration_ms": 4},
            {"event_type": "human_approval", "duration_ms": 1},
            {"event_type": "guardrail_check", "guardrail": {"passed": False}, "duration_ms": 2},
        ])
        self.assertEqual(metrics["tool_call_count"], 1)
        self.assertEqual(metrics["failed_tool_call_count"], 1)
        self.assertEqual(metrics["approval_count"], 1)
        self.assertEqual(metrics["guardrail_block_count"], 1)
        self.assertEqual(metrics["duration_ms"], 10)

    def test_trace_writes_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(path))
            trace.add(1, "agent", "tool_call", tool_call="read_file")
            trace.write()
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["metrics"]["tool_call_count"], 1)

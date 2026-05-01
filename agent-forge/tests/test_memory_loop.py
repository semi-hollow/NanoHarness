import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.cli import build_registry
from agent_forge.context.memory import Memory
from agent_forge.observability.trace import TraceRecorder
from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.runtime.observation import Observation
from agent_forge.runtime.tool_call import ToolCall


class TwoStepLLM:
    def __init__(self):
        self.calls = []

    def chat(self, messages, tools):
        self.calls.append(messages)
        if len(self.calls) == 1:
            return AgentResponse(None, [ToolCall("1", "read_file", {"path": "a.txt"})])
        return AgentResponse("done", [])


class TestMemoryLoop(unittest.TestCase):
    def test_observation_enters_next_context(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.txt").write_text("memory marker", encoding="utf-8")
            trace = TraceRecorder(str(Path(d) / "trace.json"))
            llm = TwoStepLLM()
            cfg = RuntimeConfig(workspace=d, max_steps=3, auto_approve_writes=True, trace_file=str(Path(d) / "trace.json"))
            AgentLoop(cfg, trace, build_registry(d, True), llm).run("read a file")
            trace.write()

            second_context = llm.calls[1][0].content
            self.assertIn("read_file:ok", second_context)
            self.assertIn("memory marker", second_context)

            events = json.loads((Path(d) / "trace.json").read_text(encoding="utf-8"))["events"]
            context_events = [e for e in events if e["event_type"] == "context_assembly"]
            self.assertTrue(any("read_file:ok" in e["context"]["memory_summary"] for e in context_events))

    def test_memory_does_not_cross_runs(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.txt").write_text("first-run-only", encoding="utf-8")
            cfg = RuntimeConfig(workspace=d, max_steps=3, auto_approve_writes=True, trace_file=str(Path(d) / "trace.json"))

            llm1 = TwoStepLLM()
            AgentLoop(cfg, TraceRecorder(str(Path(d) / "one.json")), build_registry(d, True), llm1).run("read once")

            llm2 = TwoStepLLM()
            AgentLoop(cfg, TraceRecorder(str(Path(d) / "two.json")), build_registry(d, True), llm2).run("read again")
            first_context_second_run = llm2.calls[0][0].content
            self.assertNotIn("read_file:ok", first_context_second_run)

    def test_memory_truncates_recent_observations(self):
        memory = Memory(n=2)
        memory.add_observation(Observation("t1", True, "one"))
        memory.add_observation(Observation("t2", True, "two"))
        memory.add_observation(Observation("t3", True, "three"))
        names = [obs.tool_name for obs in memory.recent_observations()]
        self.assertEqual(names, ["t2", "t3"])

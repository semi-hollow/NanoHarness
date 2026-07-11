import tempfile
import unittest
from pathlib import Path

from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.runtime.tool_call import ToolCall
from agent_forge.runtime.observation import Observation
from agent_forge.observability.trace import TraceRecorder
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.apply_patch import ApplyPatchTool
from agent_forge.tools.read_file import ReadFileTool
from agent_forge.tools.registry import ToolRegistry


class FinalAnswerLLM:
    last_usage = None

    def chat(self, messages, tools):
        return AgentResponse("PASS\nfinal answer", [])


class CapturingFinalLLM:
    last_usage = None

    def __init__(self):
        self.tool_names = []

    def chat(self, messages, tools):
        self.tool_names = [tool["name"] for tool in tools]
        return AgentResponse("final answer", [])


class RawToolMarkupLLM:
    last_usage = None

    def chat(self, messages, tools):
        return AgentResponse(
            '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="read_file">...</｜｜DSML｜｜tool_calls>',
            [],
        )


class RepeatReadThenFinalLLM:
    last_usage = None

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls <= 3:
            return AgentResponse(None, [ToolCall(f"read-{self.calls}", "read_file", {"path": "target.py"})])
        return AgentResponse("PASS\nused prior observation instead of reading again", [])


class RepeatPatchLLM:
    last_usage = None

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        return AgentResponse(
            None,
            [ToolCall(f"patch-{self.calls}", "apply_patch", {"path": "target.py", "old": "missing", "new": "value"})],
        )


class DiagnosticsThenFinalLLM:
    last_usage = None

    def __init__(self, kind):
        self.kind = kind
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(None, [ToolCall("diag-1", "diagnostics", {"kind": self.kind, "target": "."})])
        return AgentResponse("validation complete", [])


class SuccessfulDiagnosticsTool:
    name = "diagnostics"

    def schema(self):
        return {"name": self.name, "description": "diagnostics", "arguments": {"kind": "str", "target": "str"}}

    def execute(self, arguments):
        return Observation(self.name, True, f"{arguments['kind']} ok")


class AgentLoopPolicyTest(unittest.TestCase):
    def test_agent_name_flows_into_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            config = RuntimeConfig(workspace=tmp, max_steps=2, trace_file=str(trace_path))
            final = AgentLoop(config, trace, ToolRegistry(), FinalAnswerLLM()).run("summarize safely", agent_name="Reviewer")
            self.assertIn("final answer", final)
            agent_names = {event["agent_name"] for event in trace.events}
            self.assertIn("Reviewer", agent_names)
            self.assertTrue(any(event["event_type"] == "final_answer" for event in trace.events))

    def test_raw_tool_markup_final_answer_is_blocked_as_pending_tool_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            config = RuntimeConfig(workspace=tmp, max_steps=1, trace_file=str(trace_path))
            final = AgentLoop(config, trace, ToolRegistry(), RawToolMarkupLLM()).run("resolve a coding issue")
            self.assertIn("blocked: pending_tool_call_at_stop", final)
            stop_reasons = [event.get("stop_reason") for event in trace.events if event["event_type"] == "stop_hooks"]
            self.assertIn("pending_tool_call_at_stop", stop_reasons)

    def test_repeated_read_only_tool_call_warns_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("print('hello')\n", encoding="utf-8")
            trace_path = root / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(ReadFileTool(WorkspaceSandbox(root)))
            config = RuntimeConfig(workspace=tmp, max_steps=4, trace_file=str(trace_path))

            final = AgentLoop(config, trace, registry, RepeatReadThenFinalLLM()).run("resolve a coding issue")

            self.assertIn("used prior observation", final)
            stop_reasons = [event.get("stop_reason") for event in trace.events if event["event_type"] == "stop_hooks"]
            self.assertNotIn("repeated_tool_call", stop_reasons)
            self.assertTrue(
                any(
                    "repeated read-only tool call" in event.get("observation", "")
                    for event in trace.events
                    if event["event_type"] == "tool_observation"
                )
            )

    def test_repeated_side_effect_tool_call_still_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("print('hello')\n", encoding="utf-8")
            trace_path = root / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(ApplyPatchTool(WorkspaceSandbox(root)))
            config = RuntimeConfig(workspace=tmp, max_steps=4, trace_file=str(trace_path))

            final = AgentLoop(config, trace, registry, RepeatPatchLLM()).run("resolve a coding issue")

            self.assertEqual(final, "blocked: repeated tool call")
            stop_reasons = [event.get("stop_reason") for event in trace.events if event["event_type"] == "stop_hooks"]
            self.assertIn("repeated_tool_call", stop_reasons)

    def test_unittest_diagnostic_emits_explicit_validation_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(SuccessfulDiagnosticsTool())
            config = RuntimeConfig(workspace=tmp, max_steps=3, trace_file=str(trace_path))

            AgentLoop(config, trace, registry, DiagnosticsThenFinalLLM("unittest")).run("resolve and test a coding issue")

            validation = [event for event in trace.events if event["event_type"] == "validation_evidence"]
        self.assertEqual(len(validation), 1)
        self.assertEqual(validation[0]["validation"]["kind"], "unittest")
        self.assertEqual(validation[0]["validation"]["status"], "passed")

    def test_compile_diagnostic_is_not_counted_as_correctness_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(SuccessfulDiagnosticsTool())
            config = RuntimeConfig(workspace=tmp, max_steps=3, trace_file=str(trace_path))

            AgentLoop(config, trace, registry, DiagnosticsThenFinalLLM("compile")).run("resolve and test a coding issue")

            validation = [event for event in trace.events if event["event_type"] == "validation_evidence"]
        self.assertEqual(validation, [])

    def test_agent_loop_applies_all_tools_ablation_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            trace_path = root / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(ReadFileTool(WorkspaceSandbox(root)))
            registry.register(ApplyPatchTool(WorkspaceSandbox(root)))
            llm = CapturingFinalLLM()
            config = RuntimeConfig(
                workspace=tmp,
                max_steps=2,
                trace_file=str(trace_path),
                tool_routing_mode="all",
            )

            AgentLoop(config, trace, registry, llm).run("read only inspect target.py")

        self.assertEqual(set(llm.tool_names), {"read_file", "apply_patch"})
        context_events = [event for event in trace.events if event["event_type"] == "context_assembly"]
        self.assertIn("mode=all", context_events[0]["context"]["tool_routing"]["reason"])


if __name__ == "__main__":
    unittest.main()

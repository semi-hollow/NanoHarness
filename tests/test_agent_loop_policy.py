import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_forge.runtime.api import build_agent_loop
from agent_forge.runtime.application.tool_feedback import ToolFeedback
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.runtime.domain.conversation import Observation, ToolCall
from agent_forge.observability.api import TraceRecorder
from agent_forge.observability.domain.usage import build_usage_report
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


class PatchThenFinalLLM:
    last_usage = None

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "patch-denied",
                        "apply_patch",
                        {"path": "target.py", "old": "value = 1", "new": "value = 2"},
                    )
                ],
            )
        return AgentResponse("reported the policy block", [])


class BurstReadThenFinalLLM:
    last_usage = None

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        f"read-{index}",
                        "read_file",
                        {"path": f"target-{index}.py"},
                    )
                    for index in range(6)
                ],
            )
        return AgentResponse("bounded burst complete", [])


class CostlyReadThenFinalLLM:
    def __init__(self):
        self.calls = 0
        self.last_usage = SimpleNamespace(
            estimated_cost_usd=0.0,
            to_dict=lambda: {"estimated_cost_usd": 0.0},
        )

    def chat(self, messages, tools):
        self.calls += 1
        self.last_usage = SimpleNamespace(
            estimated_cost_usd=0.06,
            to_dict=lambda: {"estimated_cost_usd": 0.06},
        )
        if self.calls == 1:
            return AgentResponse(
                None,
                [ToolCall("read-cost", "read_file", {"path": "target.py"})],
            )
        return AgentResponse("this answer should be blocked by cumulative cost", [])


class CostlyModelFailureLLM:
    def __init__(self):
        self.last_usage = SimpleNamespace(
            estimated_cost_usd=0.02,
            to_dict=lambda: {
                "estimated_cost_usd": 0.02,
                "prompt_tokens": 20,
                "completion_tokens": 1,
                "total_tokens": 21,
            },
        )

    def chat(self, messages, tools):
        return AgentResponse(
            None,
            [],
            {"code": "provider_transport_error", "message": "timeout"},
        )


class SuccessfulDiagnosticsTool:
    name = "diagnostics"

    def schema(self):
        return {"name": self.name, "description": "diagnostics", "arguments": {"kind": "str", "target": "str"}}

    def execute(self, arguments):
        kind = arguments["kind"]
        return Observation(
            self.name,
            True,
            f"validation_command=python -m {kind} .\n{kind} ok",
        )


class AgentLoopPolicyTest(unittest.TestCase):
    def test_agent_name_flows_into_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            config = RuntimeConfig(workspace=tmp, max_steps=2, trace_file=str(trace_path))
            final = build_agent_loop(config, trace, ToolRegistry(), FinalAnswerLLM()).run("summarize safely", agent_name="Reviewer")
            self.assertIn("final answer", final)
            agent_names = {event["agent_name"] for event in trace.events}
            self.assertIn("Reviewer", agent_names)
            self.assertTrue(any(event["event_type"] == "final_answer" for event in trace.events))

    def test_raw_tool_markup_final_answer_is_blocked_as_pending_tool_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            config = RuntimeConfig(workspace=tmp, max_steps=1, trace_file=str(trace_path))
            final = build_agent_loop(config, trace, ToolRegistry(), RawToolMarkupLLM()).run("resolve a coding issue")
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

            final = build_agent_loop(config, trace, registry, RepeatReadThenFinalLLM()).run("resolve a coding issue")

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

    def test_tool_call_burst_is_bounded_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(6):
                (root / f"target-{index}.py").write_text(
                    "value = 1\n",
                    encoding="utf-8",
                )
            trace_path = root / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(ReadFileTool(WorkspaceSandbox(root)))
            config = RuntimeConfig(
                workspace=tmp,
                max_steps=2,
                max_tool_calls_per_turn=2,
                trace_file=str(trace_path),
            )

            final = build_agent_loop(
                config,
                trace,
                registry,
                BurstReadThenFinalLLM(),
            ).run("read target.py")

        self.assertIn("bounded burst", final)
        budget_events = [
            event for event in trace.events if event["event_type"] == "tool_calls_bounded"
        ]
        self.assertEqual(len(budget_events), 1)
        self.assertEqual(budget_events[0]["tool_call_budget"]["limit"], 2)
        executed = [
            event for event in trace.events if event["event_type"] == "tool_call"
        ]
        self.assertEqual(len(executed), 2)

    def test_cost_budget_uses_cumulative_run_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            trace_path = root / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(ReadFileTool(WorkspaceSandbox(root)))
            llm = CostlyReadThenFinalLLM()
            config = RuntimeConfig(
                workspace=tmp,
                max_steps=3,
                cost_budget_usd=0.1,
                trace_file=str(trace_path),
            )

            final = build_agent_loop(config, trace, registry, llm).run(
                "read target.py"
            )

        self.assertEqual(llm.calls, 2)
        self.assertEqual(final, "blocked: cost budget exceeded")
        stop_reasons = [
            event.get("stop_reason")
            for event in trace.events
            if event["event_type"] == "stop_hooks"
        ]
        self.assertIn("cost_budget_exceeded", stop_reasons)

    def test_failed_model_invocation_is_still_reported_and_costed(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            config = RuntimeConfig(
                workspace=tmp,
                max_steps=2,
                trace_file=str(trace_path),
            )

            final = build_agent_loop(
                config,
                trace,
                ToolRegistry(),
                CostlyModelFailureLLM(),
            ).run("inspect safely")
            usage = build_usage_report(
                {
                    "run_id": trace.run_id,
                    "task": trace.task,
                    "stop_reason": trace.stop_reason,
                    "final_answer": trace.final_answer,
                    "events": trace.events,
                }
            )

        self.assertIn("invalid llm response", final)
        self.assertEqual(usage["summary"]["llm_calls"], 1)
        self.assertEqual(usage["summary"]["estimated_cost_usd"], 0.02)
        llm_calls = [
            call
            for step in usage["steps"]
            for call in step["llm_calls"]
        ]
        self.assertEqual(
            llm_calls[0]["response_summary"],
            "error:provider_transport_error",
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

            final = build_agent_loop(config, trace, registry, RepeatPatchLLM()).run("resolve a coding issue")

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

            build_agent_loop(config, trace, registry, DiagnosticsThenFinalLLM("unittest")).run("resolve and test a coding issue")

            validation = [event for event in trace.events if event["event_type"] == "validation_evidence"]
        self.assertEqual(len(validation), 1)
        self.assertEqual(validation[0]["validation"]["kind"], "unittest")
        self.assertEqual(validation[0]["validation"]["status"], "passed")

    def test_pytest_diagnostic_emits_explicit_validation_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(SuccessfulDiagnosticsTool())
            config = RuntimeConfig(workspace=tmp, max_steps=3, trace_file=str(trace_path))

            build_agent_loop(config, trace, registry, DiagnosticsThenFinalLLM("pytest")).run(
                "resolve a SWE-bench coding issue"
            )

            validation = [event for event in trace.events if event["event_type"] == "validation_evidence"]
        self.assertEqual(len(validation), 1)
        self.assertEqual(validation[0]["validation"]["kind"], "pytest")
        self.assertEqual(validation[0]["validation"]["status"], "passed")

    def test_diagnostic_without_runner_command_is_not_validation_evidence(self):
        evidence = ToolFeedback.validation_evidence(
            "diagnostics",
            {"kind": "unittest", "target": "test_pytest_style.py"},
            Observation("diagnostics", True, "python test_pytest_style.py exited 0"),
        )

        self.assertIsNone(evidence)

    def test_compile_diagnostic_is_not_counted_as_correctness_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(SuccessfulDiagnosticsTool())
            config = RuntimeConfig(workspace=tmp, max_steps=3, trace_file=str(trace_path))

            build_agent_loop(config, trace, registry, DiagnosticsThenFinalLLM("compile")).run("resolve and test a coding issue")

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

            build_agent_loop(config, trace, registry, llm).run("read only inspect target.py")

        self.assertEqual(set(llm.tool_names), {"read_file", "apply_patch"})
        context_events = [event for event in trace.events if event["event_type"] == "context_assembly"]
        self.assertIn("mode=all", context_events[0]["context"]["tool_routing"]["reason"])

    def test_policy_denial_becomes_observation_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.py"
            target.write_text("value = 1\n", encoding="utf-8")
            trace = TraceRecorder(str(root / "trace.json"))
            registry = ToolRegistry()
            registry.register(ApplyPatchTool(WorkspaceSandbox(root)))
            config = RuntimeConfig(
                workspace=tmp,
                max_steps=3,
                approval_mode="locked",
                trace_file=str(root / "trace.json"),
            )

            final = build_agent_loop(config, trace, registry, PatchThenFinalLLM()).run(
                "implement the requested update in target.py"
            )
            target_content = target.read_text(encoding="utf-8")

        self.assertIn("reported the policy block", final)
        self.assertEqual(target_content, "value = 1\n")
        denials = [
            event
            for event in trace.events
            if event["event_type"] == "permission_check"
            and event.get("permission_decision") == "deny"
        ]
        self.assertEqual(len(denials), 1)


if __name__ == "__main__":
    unittest.main()

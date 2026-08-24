import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_forge.runtime.api import build_agent_loop
from agent_forge.runtime.application.tool_feedback import ToolFeedback
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.application.step_control import ExecutionBudget, FailureKind, StepController
from agent_forge.runtime.adapters.openai_compatible import AgentResponse
from agent_forge.runtime.domain.conversation import Observation, ToolCall
from agent_forge.observability.api import TraceRecorder
from agent_forge.observability.domain.usage import build_usage_report
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.builtins.replace_text import ReplaceTextTool
from agent_forge.tools.builtins.read_file import ReadFileTool
from agent_forge.tools.registry import ToolRegistry
from tests.support import StaticResponseModel, bind_new_runtime_turn


class RawToolMarkupLLM:
    last_usage = None

    def chat(self, messages, tools):
        return AgentResponse(
            '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="read_file">...</｜｜DSML｜｜tool_calls>',
            [],
        )


class StructuredFinalToolCallLLM:
    last_usage = None

    def chat(self, messages, tools):
        return AgentResponse(
            None,
            [ToolCall("final-read", "read_file", {"path": "target.py"})],
        )


class CaptureFinalTurnControlLLM:
    """记录每轮真实模型输入，验证 Runtime 的最终收口消息。"""

    last_usage = None

    def __init__(self):
        self.calls = 0
        self.requests = []

    def chat(self, messages, tools):
        self.calls += 1
        self.requests.append((list(messages), list(tools)))
        if self.calls == 1:
            return AgentResponse(
                None,
                [ToolCall("read-1", "read_file", {"path": "target.py"})],
            )
        return AgentResponse("PASS\nfinal answer", [])


class RepeatReadThenFinalLLM:
    last_usage = None

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls <= 3:
            return AgentResponse(
                None,
                [ToolCall(f"read-{self.calls}", "read_file", {"path": "target.py"})],
            )
        return AgentResponse(
            "PASS\nused prior observation instead of reading again", []
        )


class RepeatPatchLLM:
    last_usage = None

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        return AgentResponse(
            None,
            [
                ToolCall(
                    f"replace-{self.calls}",
                    "replace_text",
                    {"path": "target.py", "old": "missing", "new": "value"},
                )
            ],
        )


class ValidationThenFinalLLM:
    last_usage = None

    def __init__(self, check_type):
        self.check_type = check_type
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "validation-1",
                        "python_validation",
                        {
                            "check_type": self.check_type,
                            "validation_target": ".",
                        },
                    )
                ],
            )
        return AgentResponse("validation complete", [])


class ReplaceThenFinalLLM:
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
                        "replace-denied",
                        "replace_text",
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


class SuccessfulPythonValidationTool:
    name = "python_validation"

    def schema(self):
        return {
            "name": self.name,
            "description": "Python validation",
            "arguments": {
                "check_type": "str",
                "validation_target": "str",
            },
        }

    def execute(self, arguments):
        check_type = arguments["check_type"]
        return Observation(
            self.name,
            True,
            f"validation_command=python -m {check_type} .\n{check_type} ok",
        )


class ValidationFailureRecoveryTool(SuccessfulPythonValidationTool):
    """前三次返回测试红灯，第四次返回通过；工具执行本身始终正常。"""

    def __init__(self):
        self.calls = 0

    def execute(self, arguments):
        self.calls += 1
        passed = self.calls >= 4
        return Observation(
            tool_name=self.name,
            success=passed,
            content=(
                "validation_command=python -m pytest "
                f"{arguments['validation_target']}\n"
                f"exit_code={0 if passed else 1}"
            ),
            execution_succeeded=True,
        )


class ValidationFailureRecoveryLLM:
    """每轮消费一条失败证据，最终在验证通过后结束。"""

    last_usage = None

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls <= 4:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        f"validation-{self.calls}",
                        "python_validation",
                        {
                            "check_type": "pytest",
                            "validation_target": f"tests/stage_{self.calls}.py",
                        },
                    )
                ],
            )
        return AgentResponse("PASS\nvalidation-driven repair converged", [])


def _run_direct_agent_loop(
    config,
    trace,
    registry,
    model,
    task,
    *,
    agent_name="CodingAgent",
):
    """Direct Runtime tests 也必须从 canonical user Thread/Turn 进入。"""

    runtime = bind_new_runtime_turn(
        config,
        trace,
        task,
        agent_name=agent_name,
    )
    return build_agent_loop(runtime.config, trace, registry, model).run(
        agent_name=agent_name
    )


class AgentLoopPolicyTest(unittest.TestCase):
    def test_final_turn_has_no_tools_and_explicit_runtime_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            trace_path = root / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(ReadFileTool(WorkspaceSandbox(root)))
            model = CaptureFinalTurnControlLLM()

            final = _run_direct_agent_loop(
                RuntimeConfig(
                    workspace=tmp,
                    max_steps=2,
                    trace_file=str(trace_path),
                ),
                trace,
                registry,
                model,
                "resolve this coding issue",
            )

            self.assertIn("final answer", final)
            self.assertEqual(len(model.requests), 2)
            final_messages, final_tools = model.requests[1]
            self.assertEqual(final_tools, [])
            self.assertEqual(final_messages[-1].role, "user")
            self.assertIn("Tool execution is closed", final_messages[-1].content)

    def test_agent_name_flows_into_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            config = RuntimeConfig(
                workspace=tmp, max_steps=2, trace_file=str(trace_path)
            )
            final = _run_direct_agent_loop(
                config,
                trace,
                ToolRegistry(),
                StaticResponseModel("PASS\nfinal answer"),
                "summarize safely",
                agent_name="Reviewer",
            )
            self.assertIn("final answer", final)
            agent_names = {event["agent_name"] for event in trace.events}
            self.assertIn("Reviewer", agent_names)
            self.assertTrue(
                any(
                    event["event_type"] == "candidate_final_answer"
                    for event in trace.events
                )
            )

    def test_raw_tool_markup_final_answer_is_blocked_as_pending_tool_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            config = RuntimeConfig(
                workspace=tmp, max_steps=1, trace_file=str(trace_path)
            )
            final = _run_direct_agent_loop(
                config,
                trace,
                ToolRegistry(),
                RawToolMarkupLLM(),
                "resolve a coding issue",
            )
            self.assertIn("blocked: pending_tool_call_at_stop", final)
            stop_reasons = [
                event.get("stop_reason")
                for event in trace.events
                if event["event_type"] == "stop_hooks"
            ]
            self.assertIn("pending_tool_call_at_stop", stop_reasons)
            rejected_requests = [
                event
                for event in trace.events
                if event["event_type"] == "pending_tool_call_rejected"
            ]
            self.assertEqual(len(rejected_requests), 1)
            self.assertEqual(rejected_requests[0]["tool_call"], "read_file")
            self.assertFalse(rejected_requests[0]["success"])
            self.assertFalse(
                any(
                    event["event_type"] == "final_answer"
                    and event.get("pending_tool_call")
                    for event in trace.events
                )
            )

    def test_structured_tool_call_on_final_turn_uses_same_blocked_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            trace_path = root / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(ReadFileTool(WorkspaceSandbox(root)))

            final = _run_direct_agent_loop(
                RuntimeConfig(
                    workspace=tmp,
                    max_steps=1,
                    trace_file=str(trace_path),
                ),
                trace,
                registry,
                StructuredFinalToolCallLLM(),
                "resolve a coding issue",
            )

            self.assertIn("blocked: pending_tool_call_at_stop", final)
            self.assertFalse(
                any(event["event_type"] == "tool_call" for event in trace.events)
            )
            rejected = [
                event
                for event in trace.events
                if event["event_type"] == "pending_tool_call_rejected"
            ]
            self.assertEqual(rejected[0]["tool_call"], "read_file")

    def test_repeated_read_only_tool_call_warns_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("print('hello')\n", encoding="utf-8")
            trace_path = root / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(ReadFileTool(WorkspaceSandbox(root)))
            config = RuntimeConfig(
                workspace=tmp, max_steps=4, trace_file=str(trace_path)
            )

            final = _run_direct_agent_loop(
                config,
                trace,
                registry,
                RepeatReadThenFinalLLM(),
                "resolve a coding issue",
            )

            self.assertIn("used prior observation", final)
            stop_reasons = [
                event.get("stop_reason")
                for event in trace.events
                if event["event_type"] == "stop_hooks"
            ]
            self.assertNotIn("repeated_tool_call", stop_reasons)
            self.assertTrue(
                any(
                    "skipped consecutive identical call" in event.get("observation", "")
                    for event in trace.events
                    if event["event_type"] == "tool_observation"
                )
            )
            executed_read_calls = [
                event
                for event in trace.events
                if event["event_type"] == "tool_call"
                and event.get("tool_call") == "read_file"
            ]
            self.assertEqual(len(executed_read_calls), 2)
            routed_guardrail_checks = [
                event
                for event in trace.events
                if event["event_type"] == "guardrail_check"
                and event.get("guardrail", {}).get("category") == "tool"
            ]
            self.assertTrue(routed_guardrail_checks)
            self.assertTrue(
                all(event["guardrail"]["passed"] for event in routed_guardrail_checks)
            )

    def test_repeat_limit_resets_after_a_different_tool_intent(self):
        controller = StepController(
            budget=ExecutionBudget(max_consecutive_identical_tool_calls=2)
        )
        first_read = ToolCall("read-1", "read_file", {"path": "target.py"})
        same_read_retry = ToolCall(
            "read-2",
            "read_file",
            {"path": "target.py"},
        )
        different_read = ToolCall("read-3", "read_file", {"path": "other.py"})

        self.assertIsNone(controller.observe_tool_intent_for_repeat_limit(first_read))
        self.assertIsNone(
            controller.observe_tool_intent_for_repeat_limit(same_read_retry)
        )
        self.assertIsNone(
            controller.observe_tool_intent_for_repeat_limit(different_read)
        )
        self.assertIsNone(controller.observe_tool_intent_for_repeat_limit(first_read))
        self.assertIsNone(
            controller.observe_tool_intent_for_repeat_limit(same_read_retry)
        )
        self.assertIsNotNone(
            controller.observe_tool_intent_for_repeat_limit(first_read)
        )

    def test_consecutive_failure_stop_reason_includes_count_and_limit(self):
        controller = StepController(
            budget=ExecutionBudget(max_consecutive_failures=3)
        )
        for failure_number in range(1, 4):
            controller.classify_observation(
                Observation(
                    tool_name=f"tool-{failure_number}",
                    success=False,
                    content=f"exit_code={failure_number}",
                )
            )

        stop_signal = controller.should_stop(step=1)

        self.assertIsNotNone(stop_signal)
        self.assertEqual(
            stop_signal.reason,
            "too many consecutive failed tools: 3 >= limit 3",
        )

    def test_failed_validations_do_not_trip_tool_infrastructure_breaker(self):
        controller = StepController(
            budget=ExecutionBudget(max_consecutive_failures=3)
        )

        for _ in range(4):
            recovery_signal = controller.classify_observation(
                Observation(
                    tool_name="python_validation",
                    success=False,
                    content="validation_command=python -m pytest tests\nexit_code=1",
                    execution_succeeded=True,
                )
            )

        self.assertEqual(recovery_signal.kind, FailureKind.VALIDATION_FAILED)
        self.assertIsNone(controller.should_stop(step=1))

    def test_agent_loop_can_converge_after_three_failed_validations(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(ValidationFailureRecoveryTool())

            final = _run_direct_agent_loop(
                RuntimeConfig(
                    workspace=tmp,
                    max_steps=6,
                    max_consecutive_failures=3,
                    trace_file=str(trace_path),
                ),
                trace,
                registry,
                ValidationFailureRecoveryLLM(),
                "repair the failing tests and validate the result",
            )
            usage = build_usage_report(
                {
                    "run_id": trace.run_id,
                    "task": trace.task,
                    "stop_reason": trace.stop_reason,
                    "final_answer": trace.final_answer,
                    "events": trace.events,
                }
            )

        self.assertIn("converged", final)
        self.assertEqual(usage["summary"]["failed_tool_calls"], 0)
        self.assertEqual(usage["summary"]["tool_calls"], 4)
        self.assertEqual(usage["summary"]["failed_validations"], 3)
        self.assertEqual(
            sum(
                event["event_type"] == "validation_evidence"
                and event.get("validation", {}).get("status") == "failed"
                for event in trace.events
            ),
            3,
        )
        self.assertEqual(
            sum(
                event["event_type"] == "tool_observation"
                and event.get("tool_call") == "python_validation"
                for event in trace.events
            ),
            4,
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

            final = _run_direct_agent_loop(
                config,
                trace,
                registry,
                BurstReadThenFinalLLM(),
                "read target.py",
            )

        self.assertIn("bounded burst", final)
        budget_events = [
            event
            for event in trace.events
            if event["event_type"] == "tool_calls_bounded"
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

            final = _run_direct_agent_loop(
                config,
                trace,
                registry,
                llm,
                "read target.py",
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

            final = _run_direct_agent_loop(
                config,
                trace,
                ToolRegistry(),
                CostlyModelFailureLLM(),
                "inspect safely",
            )
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
        llm_calls = [call for step in usage["steps"] for call in step["llm_calls"]]
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
            registry.register(ReplaceTextTool(WorkspaceSandbox(root)))
            config = RuntimeConfig(
                workspace=tmp, max_steps=4, trace_file=str(trace_path)
            )

            final = _run_direct_agent_loop(
                config,
                trace,
                registry,
                RepeatPatchLLM(),
                "resolve a coding issue",
            )

            self.assertEqual(final, "blocked: repeated tool call")
            stop_reasons = [
                event.get("stop_reason")
                for event in trace.events
                if event["event_type"] == "stop_hooks"
            ]
            self.assertIn("repeated_tool_call", stop_reasons)

    def test_unittest_diagnostic_emits_explicit_validation_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(SuccessfulPythonValidationTool())
            config = RuntimeConfig(
                workspace=tmp,
                max_steps=3,
                trace_file=str(trace_path),
                skill_mode="none",
            )

            _run_direct_agent_loop(
                config,
                trace,
                registry,
                ValidationThenFinalLLM("unittest"),
                "resolve and test a coding issue",
            )

            validation = [
                event
                for event in trace.events
                if event["event_type"] == "validation_evidence"
            ]
        self.assertEqual(len(validation), 1)
        self.assertEqual(validation[0]["validation"]["kind"], "unittest")
        self.assertEqual(validation[0]["validation"]["status"], "passed")

    def test_pytest_diagnostic_emits_explicit_validation_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(SuccessfulPythonValidationTool())
            config = RuntimeConfig(
                workspace=tmp,
                max_steps=3,
                trace_file=str(trace_path),
                skill_mode="none",
            )

            _run_direct_agent_loop(
                config,
                trace,
                registry,
                ValidationThenFinalLLM("pytest"),
                "resolve a SWE-bench coding issue",
            )

            validation = [
                event
                for event in trace.events
                if event["event_type"] == "validation_evidence"
            ]
        self.assertEqual(len(validation), 1)
        self.assertEqual(validation[0]["validation"]["kind"], "pytest")
        self.assertEqual(validation[0]["validation"]["status"], "passed")

    def test_validation_without_runner_command_is_not_validation_evidence(self):
        evidence = ToolFeedback.build_validation_evidence(
            "python_validation",
            {
                "check_type": "unittest",
                "validation_target": "test_pytest_style.py",
            },
            Observation(
                "python_validation",
                True,
                "python test_pytest_style.py exited 0",
            ),
        )

        self.assertIsNone(evidence)

    def test_compile_diagnostic_is_not_counted_as_correctness_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(SuccessfulPythonValidationTool())
            config = RuntimeConfig(
                workspace=tmp, max_steps=3, trace_file=str(trace_path)
            )

            _run_direct_agent_loop(
                config,
                trace,
                registry,
                ValidationThenFinalLLM("compile"),
                "resolve and test a coding issue",
            )

            validation = [
                event
                for event in trace.events
                if event["event_type"] == "validation_evidence"
            ]
        self.assertEqual(validation, [])

    def test_agent_loop_applies_explicit_all_tools_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            trace_path = root / "trace.json"
            trace = TraceRecorder(str(trace_path))
            registry = ToolRegistry()
            registry.register(ReadFileTool(WorkspaceSandbox(root)))
            registry.register(ReplaceTextTool(WorkspaceSandbox(root)))
            llm = StaticResponseModel("final answer")
            config = RuntimeConfig(
                workspace=tmp,
                max_steps=2,
                trace_file=str(trace_path),
                tool_routing_mode="all",
            )

            _run_direct_agent_loop(
                config,
                trace,
                registry,
                llm,
                "read only inspect target.py",
            )

        self.assertEqual(set(llm.tool_names), {"read_file", "replace_text"})
        context_events = [
            event
            for event in trace.events
            if event["event_type"] == "context_assembly"
            and "tool_routing" in event.get("context", {})
        ]
        self.assertIn(
            "mode=all", context_events[0]["context"]["tool_routing"]["reason"]
        )

    def test_policy_denial_becomes_observation_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.py"
            target.write_text("value = 1\n", encoding="utf-8")
            trace = TraceRecorder(str(root / "trace.json"))
            registry = ToolRegistry()
            registry.register(ReplaceTextTool(WorkspaceSandbox(root)))
            config = RuntimeConfig(
                workspace=tmp,
                max_steps=3,
                approval_mode="locked",
                trace_file=str(root / "trace.json"),
            )

            final = _run_direct_agent_loop(
                config,
                trace,
                registry,
                ReplaceThenFinalLLM(),
                "implement the requested update in target.py",
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

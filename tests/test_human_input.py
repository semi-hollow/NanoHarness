import tempfile
import unittest
from pathlib import Path

from agent_forge.cli.parser import build_parser
from agent_forge.observability.api import TraceRecorder
from agent_forge.runtime.adapters import (
    JsonHumanInputRepository,
    JsonTaskStateRepository,
)
from agent_forge.runtime.api import build_agent_loop
from agent_forge.runtime.application.operator_control import BuildContinuationPlan
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import ToolCall
from agent_forge.runtime.domain.human_input import HumanInputRequestDraft
from agent_forge.runtime.domain.task import (
    TaskCheckpointUpdate,
    TaskRunStatus,
    TaskStartRequest,
)
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.runtime.wiring import ToolRegistryBuildRequest, build_registry
from agent_forge.tools.ask_human import AskHumanTool
from agent_forge.tools.registry import ToolRegistry
from tests.support import StaticResponseModel


class NeverCalledLLM:
    last_usage = None

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        raise AssertionError("model must not run while clarification is unresolved")


class AskThenFinalLLM:
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
                        "ask-1",
                        "ask_human",
                        {"question": "Which API version should be used?"},
                    )
                ],
            )
        return AgentResponse("finished", [])


class WriteThenAskLLM:
    last_usage = None

    def chat(self, messages, tools):
        return AgentResponse(
            None,
            [
                ToolCall(
                    "write-before-question",
                    "write_file",
                    {
                        "path": "result.txt",
                        "content": "must not be written before the answer\n",
                    },
                ),
                ToolCall(
                    "ask-after-write",
                    "ask_human",
                    {"question": "Which compatibility target should be used?"},
                ),
            ],
        )


class InvalidChoicesThenFinalLLM:
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
                        "ask-invalid-choices",
                        "ask_human",
                        {"question": "Choose a target", "choices": "api"},
                    )
                ],
            )
        return AgentResponse("finished after invalid arguments were rejected", [])


class HumanInputTest(unittest.TestCase):
    def test_human_control_signal_defers_same_turn_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = TraceRecorder(str(root / "trace.json"))
            config = RuntimeConfig(
                workspace=tmp,
                max_steps=2,
                task_state_root=str(root / "task_state"),
                human_input_root=str(root / "human_input"),
            )

            final = build_agent_loop(
                config,
                trace,
                build_registry(ToolRegistryBuildRequest(tmp, auto=True)),
                WriteThenAskLLM(),
            ).run("implement a compatibility update in result.txt")

            self.assertIn("waiting_human", final)
            self.assertFalse((root / "result.txt").exists())
            self.assertEqual(
                len(JsonHumanInputRepository(root / "human_input").list_pending()), 1
            )
            deferred = [
                event
                for event in trace.events
                if event["event_type"] == "tool_calls_deferred_for_human_input"
            ]
            self.assertEqual(deferred[0]["deferred_tools"], ["write_file"])

    def test_invalid_tool_level_choices_do_not_create_a_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = TraceRecorder(str(root / "trace.json"))
            llm = InvalidChoicesThenFinalLLM()
            config = RuntimeConfig(
                workspace=tmp,
                max_steps=3,
                task_state_root=str(root / "task_state"),
                human_input_root=str(root / "human_input"),
            )

            final = build_agent_loop(
                config,
                trace,
                build_registry(ToolRegistryBuildRequest(tmp, auto=True)),
                llm,
            ).run("inspect the compatibility target and ask when needed")

            self.assertIn("finished after invalid arguments were rejected", final)
            self.assertEqual(
                JsonHumanInputRepository(root / "human_input").list_all(), []
            )
            observations = [
                event.get("observation", "")
                for event in trace.events
                if event["event_type"] == "tool_observation"
            ]
            self.assertTrue(
                any("choices must be list" in item for item in observations)
            )

    def test_store_persists_response_and_cancelled_requests_are_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonHumanInputRepository(tmp)
            request = store.request(
                HumanInputRequestDraft(
                    thread_id="thread-1",
                    kind="clarification",
                    question="Which target?",
                    choices=("api", "cli"),
                    workspace=tmp,
                    run_id="run-1",
                    step=0,
                    agent_name="CodingAgent",
                    reason="ambiguous target",
                )
            )
            self.assertEqual(request.status, "pending")
            self.assertEqual(len(store.list_pending()), 1)

            responded = store.respond(request.request_id, "api")
            self.assertEqual(responded.status, "responded")
            self.assertEqual(store.get(request.request_id).answer, "api")

            other = store.request(
                HumanInputRequestDraft(
                    thread_id="thread-2",
                    kind="clarification",
                    question="Continue?",
                    choices=(),
                    workspace=tmp,
                    run_id="run-2",
                    step=0,
                    agent_name="CodingAgent",
                    reason="operator choice",
                )
            )
            cancelled = store.cancel(other.request_id, "operator stopped the run")
            self.assertEqual(cancelled.status, "cancelled")
            with self.assertRaisesRegex(ValueError, "terminal"):
                store.respond(other.request_id, "yes")

            changed_choices = store.request(
                HumanInputRequestDraft(
                    thread_id="thread-1",
                    kind="clarification",
                    question="Which target?",
                    choices=("worker", "api"),
                    workspace=tmp,
                    run_id="run-3",
                    step=1,
                    agent_name="CodingAgent",
                    reason="target options changed",
                )
            )
            self.assertNotEqual(changed_choices.request_id, request.request_id)
            self.assertEqual(changed_choices.status, "pending")

            with self.assertRaisesRegex(ValueError, "invalid human input request id"):
                store.get("../../outside")

    def test_preloop_clarification_persists_request_before_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            llm = NeverCalledLLM()
            trace = TraceRecorder(str(root / "trace.json"))
            config = RuntimeConfig(
                workspace=tmp,
                max_steps=2,
                trace_file=str(root / "trace.json"),
                task_state_root=str(root / "task_state"),
                human_input_root=str(root / "human_input"),
            )

            final = build_agent_loop(config, trace, ToolRegistry(), llm).run("fix it")

            self.assertIn("waiting_human", final)
            self.assertEqual(llm.calls, 0)
            request = JsonHumanInputRepository(root / "human_input").list_pending()[0]
            checkpoint = JsonTaskStateRepository(root / "task_state").list()[0]
            self.assertEqual(checkpoint.status, TaskRunStatus.WAITING_HUMAN.value)
            self.assertEqual(
                checkpoint.metadata["human_input_request_id"], request.request_id
            )
            self.assertTrue(
                any(
                    event["event_type"] == "human_input_requested"
                    for event in trace.events
                )
            )

    def test_tool_level_question_stops_without_executing_synthetic_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = ToolRegistry()
            registry.register(AskHumanTool())
            trace = TraceRecorder(str(root / "trace.json"))
            config = RuntimeConfig(
                workspace=tmp,
                max_steps=2,
                trace_file=str(root / "trace.json"),
                task_state_root=str(root / "task_state"),
                human_input_root=str(root / "human_input"),
            )

            final = build_agent_loop(config, trace, registry, AskThenFinalLLM()).run(
                "clarify the API version for this project"
            )

            self.assertIn("waiting_human", final)
            request = JsonHumanInputRepository(root / "human_input").list_pending()[0]
            self.assertEqual(request.question, "Which API version should be used?")
            direct = AskHumanTool().execute({"question": "unsafe direct call"})
            self.assertFalse(direct.success)
            self.assertIn("AgentLoop", direct.content)

    def test_resume_cli_and_continuation_context_use_persisted_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = JsonHumanInputRepository(root / "human_input")
            request = store.request(
                HumanInputRequestDraft(
                    thread_id="thread-1",
                    kind="clarification",
                    question="Which target?",
                    choices=(),
                    workspace=tmp,
                    run_id="run-1",
                    step=0,
                    agent_name="CodingAgent",
                    reason="ambiguous target",
                )
            )
            checkpoint_store = JsonTaskStateRepository(root / "task_state")
            checkpoint = checkpoint_store.start(
                TaskStartRequest(
                    run_id="run-1",
                    task="fix it",
                    workspace=tmp,
                    agent_name="CodingAgent",
                    metadata={
                        "human_thread_id": "thread-1",
                        "human_input_request_id": request.request_id,
                    },
                )
            )
            checkpoint_store.update(
                checkpoint,
                TaskCheckpointUpdate(status=TaskRunStatus.WAITING_HUMAN),
            )

            args = build_parser().parse_args(
                [
                    "resume",
                    str(root / "previous-run"),
                    "--answer",
                    "Update agent_forge/runtime/config.py",
                    "--request-id",
                    request.request_id,
                    "--human-input-root",
                    str(root / "human_input"),
                ]
            )
            self.assertEqual(args.command, "resume")
            store.respond(args.request_id, args.answer)

            plan = BuildContinuationPlan(store).execute(
                checkpoint,
                override_task="",
            )
            self.assertIn("Update agent_forge/runtime/config.py", plan.task)
            self.assertIn("Which target?", plan.task)
            self.assertEqual(plan.human_thread_id, "thread-1")

    def test_existing_response_continues_but_cancelled_question_stays_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            human_root = root / "human_input"
            first_config = RuntimeConfig(
                workspace=tmp,
                max_steps=2,
                task_state_root=str(root / "first-state"),
                human_input_root=str(human_root),
                human_thread_id="stable-thread",
            )
            first = build_agent_loop(
                first_config,
                TraceRecorder(str(root / "first-trace.json")),
                ToolRegistry(),
                NeverCalledLLM(),
            ).run("fix it")
            self.assertIn("waiting_human", first)
            request = JsonHumanInputRepository(human_root).list_pending()[0]
            JsonHumanInputRepository(human_root).respond(
                request.request_id, "Update config.py"
            )

            final_llm = StaticResponseModel("finished with operator input")
            second_config = RuntimeConfig(
                workspace=tmp,
                max_steps=2,
                task_state_root=str(root / "second-state"),
                human_input_root=str(human_root),
                human_thread_id="stable-thread",
            )
            final = build_agent_loop(
                second_config,
                TraceRecorder(str(root / "second-trace.json")),
                ToolRegistry(),
                final_llm,
            ).run("fix it")
            self.assertIn("finished with operator input", final)
            self.assertEqual(final_llm.calls, 1)

            cancelled_thread = "cancelled-thread"
            cancelled_config = RuntimeConfig(
                workspace=tmp,
                max_steps=2,
                task_state_root=str(root / "cancel-first-state"),
                human_input_root=str(human_root),
                human_thread_id=cancelled_thread,
            )
            build_agent_loop(
                cancelled_config,
                TraceRecorder(str(root / "cancel-first-trace.json")),
                ToolRegistry(),
                NeverCalledLLM(),
            ).run("fix it")
            cancelled_request = next(
                item
                for item in JsonHumanInputRepository(human_root).list_pending()
                if item.thread_id == cancelled_thread
            )
            JsonHumanInputRepository(human_root).cancel(
                cancelled_request.request_id, "operator stopped"
            )
            never = NeverCalledLLM()
            cancelled_retry = RuntimeConfig(
                workspace=tmp,
                max_steps=2,
                task_state_root=str(root / "cancel-second-state"),
                human_input_root=str(human_root),
                human_thread_id=cancelled_thread,
            )
            blocked = build_agent_loop(
                cancelled_retry,
                TraceRecorder(str(root / "cancel-second-trace.json")),
                ToolRegistry(),
                never,
            ).run("fix it")
            self.assertIn("human_input_cancelled", blocked)
            self.assertEqual(never.calls, 0)


if __name__ == "__main__":
    unittest.main()

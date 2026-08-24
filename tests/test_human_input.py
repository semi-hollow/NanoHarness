import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from apps.cli.parser import build_parser
from agent_forge.harness import Harness
from agent_forge.harness_contracts import HarnessConfig
from agent_forge.runtime.adapters import JsonHumanInputRepository
from agent_forge.runtime.adapters.openai_compatible import AgentResponse
from agent_forge.runtime.domain.conversation import ToolCall
from agent_forge.runtime.domain.human_input import HumanInputRequestDraft
from agent_forge.runtime.domain.task import TaskRunStatus
from agent_forge.runtime.wiring import ToolRegistryBuildRequest, build_registry
from agent_forge.tools.builtins.ask_human import AskHumanTool
from agent_forge.tools.registry import ToolRegistry
from tests.support import StaticResponseModel


class NeverCalledLLM:
    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        raise AssertionError("model must not run while clarification is unresolved")


class AskThenFinalLLM:
    last_usage = None

    def __init__(self) -> None:
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

    def __init__(self) -> None:
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


def _config(root: Path, *, max_steps: int = 3) -> HarnessConfig:
    return HarnessConfig(
        workspace=str(root),
        output_root=str(root / "runs"),
        conversation_thread_root=str(root / "threads"),
        approval_root=str(root / "approvals"),
        human_input_root=str(root / "human_input"),
        operation_ledger_root=str(root / "operation_ledger"),
        memory_root=str(root / "memory"),
        max_steps=max_steps,
    )


def _checkpoint_path(result) -> Path:
    return result.artifact_dir / "task_state" / f"{result.run_id}.json"


def _trace_events(result) -> list[dict[str, object]]:
    assert result.trace_path is not None
    return json.loads(result.trace_path.read_text(encoding="utf-8"))["events"]


class HumanInputTest(unittest.TestCase):
    def test_human_control_signal_defers_same_step_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = Harness(
                model=WriteThenAskLLM(),
                tools=build_registry(
                    ToolRegistryBuildRequest(workspace=tmp, auto=True)
                ),
                config=_config(root),
            ).run("implement a compatibility update in result.txt")

            self.assertEqual(result.status, TaskRunStatus.WAITING_HUMAN)
            self.assertFalse((root / "result.txt").exists())
            self.assertEqual(
                len(JsonHumanInputRepository(root / "human_input").list_pending()),
                1,
            )
            deferred = [
                event
                for event in _trace_events(result)
                if event["event_type"] == "tool_calls_deferred_for_human_input"
            ]
            self.assertEqual(deferred[0]["deferred_tools"], ["write_file"])

    def test_invalid_tool_level_choices_do_not_create_a_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            llm = InvalidChoicesThenFinalLLM()
            result = Harness(
                model=llm,
                tools=build_registry(
                    ToolRegistryBuildRequest(workspace=tmp, auto=True)
                ),
                config=_config(root),
            ).run("inspect the compatibility target and ask when needed")

            self.assertIn(
                "finished after invalid arguments were rejected",
                result.stop_output,
            )
            self.assertEqual(
                JsonHumanInputRepository(root / "human_input").list_all(),
                [],
            )
            observations = [
                event.get("observation", "")
                for event in _trace_events(result)
                if event["event_type"] == "tool_observation"
            ]
            self.assertTrue(any("choices must be list" in str(item) for item in observations))

    def test_request_identity_is_turn_scoped_and_resume_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonHumanInputRepository(tmp)
            draft = HumanInputRequestDraft(
                thread_id="thread-1",
                turn_id="turn-1",
                kind="clarification",
                question="Which target?",
                choices=("api", "cli"),
                workspace=tmp,
                run_id="run-1",
                step=0,
                agent_name="CodingAgent",
                reason="ambiguous target",
                invocation_id="assistant-1:0:ask-1",
            )
            request = store.request(draft)
            repeated = store.request(
                HumanInputRequestDraft(
                    **{**draft.__dict__, "run_id": "run-resume", "step": 1}
                )
            )
            next_turn = store.request(
                HumanInputRequestDraft(
                    **{**draft.__dict__, "turn_id": "turn-2", "run_id": "run-2"}
                )
            )
            self.assertEqual(repeated.request_id, request.request_id)
            self.assertNotEqual(next_turn.request_id, request.request_id)
            next_invocation = store.request(
                HumanInputRequestDraft(
                    **{
                        **draft.__dict__,
                        "run_id": "run-2",
                        "step": 2,
                        "invocation_id": "assistant-2:0:ask-2",
                    }
                )
            )
            self.assertNotEqual(next_invocation.request_id, request.request_id)

            responded = store.respond(request.request_id, "api")
            self.assertEqual(responded.status, "responded")
            self.assertEqual(store.get(request.request_id).answer, "api")
            self.assertEqual(
                store.respond(request.request_id, "api", "retry is ignored").answer,
                "api",
            )
            with self.assertRaisesRegex(ValueError, "immutable"):
                store.respond(request.request_id, "cli")
            with self.assertRaisesRegex(ValueError, "immutable"):
                store.cancel(request.request_id)

            other = store.request(
                HumanInputRequestDraft(
                    thread_id="thread-2",
                    turn_id="turn-2",
                    kind="clarification",
                    question="Continue?",
                    choices=(),
                    workspace=tmp,
                    run_id="run-3",
                    step=0,
                    agent_name="CodingAgent",
                    reason="operator choice",
                )
            )
            cancelled = store.cancel(other.request_id, "operator stopped the run")
            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(store.cancel(other.request_id).status, "cancelled")
            with self.assertRaisesRegex(ValueError, "terminal decision.*immutable"):
                store.respond(other.request_id, "yes")
            with self.assertRaisesRegex(ValueError, "invalid human input request id"):
                store.get("../../outside")

    def test_human_input_terminal_decision_is_concurrent_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = JsonHumanInputRepository(root / "human_input")
            request = store.request(
                HumanInputRequestDraft(
                    thread_id="thread-1",
                    turn_id="turn-1",
                    kind="clarification",
                    question="Continue?",
                    choices=("yes", "no"),
                    workspace=tmp,
                    run_id="run-1",
                    step=0,
                    agent_name="CodingAgent",
                    reason="operator choice",
                )
            )
            barrier = Barrier(2)

            def decide(action: str) -> str:
                barrier.wait()
                try:
                    if action == "respond":
                        return store.respond(request.request_id, "yes").status
                    return store.cancel(request.request_id).status
                except ValueError:
                    return "conflict"

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(decide, ["respond", "cancel"]))

            persisted = store.get(request.request_id)
            self.assertIsNotNone(persisted)
            assert persisted is not None
            self.assertIn(persisted.status, {"responded", "cancelled"})
            self.assertCountEqual(results, [persisted.status, "conflict"])

    def test_preloop_clarification_persists_request_before_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            llm = NeverCalledLLM()
            result = Harness(
                model=llm,
                tools=ToolRegistry(),
                config=_config(root),
            ).run("fix it")

            self.assertEqual(result.status, TaskRunStatus.WAITING_HUMAN)
            self.assertEqual(llm.calls, 0)
            request = JsonHumanInputRepository(root / "human_input").list_pending()[0]
            self.assertEqual(
                result.checkpoint.metadata["human_input_request_id"],
                request.request_id,
            )
            self.assertEqual(request.thread_id, result.thread_id)
            self.assertEqual(request.turn_id, result.turn_id)
            self.assertTrue(
                any(
                    event["event_type"] == "human_input_requested"
                    for event in _trace_events(result)
                )
            )

    def test_tool_level_question_stops_without_executing_synthetic_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = ToolRegistry()
            registry.register(AskHumanTool())
            result = Harness(
                model=AskThenFinalLLM(),
                tools=registry,
                config=_config(root),
            ).run("clarify the API version for this project")

            self.assertEqual(result.status, TaskRunStatus.WAITING_HUMAN)
            request = JsonHumanInputRepository(root / "human_input").list_pending()[0]
            self.assertEqual(request.question, "Which API version should be used?")
            direct = AskHumanTool().execute({"question": "unsafe direct call"})
            self.assertFalse(direct.success)
            self.assertIn("AgentLoop", direct.content)

    def test_resume_cli_answer_enters_same_turn_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            waiting = Harness(
                model=NeverCalledLLM(),
                tools=ToolRegistry(),
                config=config,
            ).run("fix it")
            store = JsonHumanInputRepository(root / "human_input")
            request = store.list_pending()[0]
            args = build_parser().parse_args(
                [
                    "resume",
                    str(waiting.artifact_dir),
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

            final_llm = StaticResponseModel("finished with operator input")
            resumed = Harness(
                model=final_llm,
                tools=ToolRegistry(),
                config=config,
            ).resume(_checkpoint_path(waiting))

            self.assertEqual(resumed.thread_id, waiting.thread_id)
            self.assertEqual(resumed.turn_id, waiting.turn_id)
            self.assertNotEqual(resumed.run_id, waiting.run_id)
            rendered = "\n".join(message.content or "" for message in final_llm.messages)
            self.assertIn("Update agent_forge/runtime/config.py", rendered)
            self.assertIn(request.question, rendered)

    def test_cancelled_question_blocks_same_turn_resume_without_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = _config(root)
            waiting = Harness(
                model=NeverCalledLLM(),
                tools=ToolRegistry(),
                config=config,
            ).run("fix it")
            store = JsonHumanInputRepository(root / "human_input")
            request = store.list_pending()[0]
            store.cancel(request.request_id, "operator stopped")
            never = NeverCalledLLM()

            blocked = Harness(
                model=never,
                tools=ToolRegistry(),
                config=config,
            ).resume(_checkpoint_path(waiting))

            self.assertEqual(blocked.thread_id, waiting.thread_id)
            self.assertEqual(blocked.turn_id, waiting.turn_id)
            self.assertIn("human_input_cancelled", blocked.stop_output)
            self.assertEqual(never.calls, 0)


if __name__ == "__main__":
    unittest.main()

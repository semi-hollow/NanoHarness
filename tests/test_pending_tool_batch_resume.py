import tempfile
import unittest
from pathlib import Path

from agent_forge.harness import Harness
from agent_forge.harness_contracts import HarnessConfig, HarnessExtensions
from agent_forge.extensions import HookDecision, HookDecisionType, RuntimeHook
from agent_forge.runtime.adapters import (
    JsonApprovalRepository,
    JsonConversationThreadRepository,
    JsonHumanInputRepository,
    JsonOperationLedgerRepository,
    JsonTaskStateRepository,
)
from agent_forge.observability.api import TraceRecorder
from agent_forge.runtime.domain.thread import (
    ConversationItemDraft,
    ConversationThread,
    ThreadRun,
    Turn,
)
from agent_forge.runtime.domain.conversation import AgentResponse, ToolCall
from agent_forge.runtime.domain.task import (
    PendingExecutionPointer,
    TaskRunStatus,
    TaskStartRequest,
)
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.wiring import (
    AgentLoopBuildRequest,
    RuntimeDependencyOverrides,
    build_agent_loop_from_request,
)
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.builtins.ask_human import AskHumanTool
from agent_forge.tools.builtins.read_file import ReadFileTool
from agent_forge.tools.builtins.replace_text import ReplaceTextTool
from agent_forge.tools.builtins.run_command import RunCommandTool
from agent_forge.tools.registry import ToolRegistry


class ApprovalBatchModel:
    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                content="I inspected the target and will now apply the edit.",
                tool_calls=[
                    ToolCall("read-1", "read_file", {"path": "target.py"}),
                    ToolCall(
                        "replace-1",
                        "replace_text",
                        {
                            "path": "target.py",
                            "old": "value = 1\n",
                            "new": "value = 2\n",
                        },
                    ),
                ],
            )
        return AgentResponse("Edit applied; validation not run.", [])


class RejectedWriteModel:
    last_usage = None

    def __init__(self) -> None:
        self.calls = 0
        self.saw_rejection = False

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                "Requesting the governed edit.",
                [
                    ToolCall(
                        "replace-1",
                        "replace_text",
                        {
                            "path": "target.py",
                            "old": "value = 1\n",
                            "new": "value = 2\n",
                        },
                    )
                ],
            )
        self.saw_rejection = any(
            message.role == "tool" and "approval rejected" in message.content
            for message in messages
        )
        return AgentResponse("Approval was rejected; no edit was made.", [])


class AskHumanBatchModel:
    last_usage = None

    def __init__(self) -> None:
        self.calls = 0
        self.saw_preceding_context = False

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                "PRE_HUMAN_CONTEXT_7319: I need one operator choice before continuing.",
                [
                    ToolCall("read-before", "read_file", {"path": "target.py"}),
                    ToolCall(
                        "ask-1",
                        "ask_human",
                        {"question": "Choose runtime", "choices": ["3.11", "3.12"]},
                    ),
                    ToolCall("read-after", "read_file", {"path": "target.py"}),
                ],
            )
        self.saw_preceding_context = any(
            message.role == "assistant"
            and "PRE_HUMAN_CONTEXT_7319" in message.content
            for message in messages
        )
        return AgentResponse("Operator chose a runtime; no edit was needed.", [])


class RepeatedHumanQuestionModel:
    """后续独立 ask_human 即使文本相同，也必须形成新的人工请求。"""

    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls <= 2:
            return AgentResponse(
                f"Question pass {self.calls}.",
                [
                    ToolCall(
                        f"ask-{self.calls}",
                        "ask_human",
                        {"question": "Choose runtime", "choices": ["3.11", "3.12"]},
                    )
                ],
            )
        return AgentResponse("Both operator decisions were collected.", [])


class BudgetedReadModel:
    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                "Reading three views.",
                [
                    ToolCall("read-1", "read_file", {"path": "target.py"}),
                    ToolCall("read-2", "read_file", {"path": "target.py"}),
                    ToolCall("read-3", "read_file", {"path": "target.py"}),
                ],
            )
        return AgentResponse("One read executed; two were budget-skipped.", [])


class ReusedCallIdModel:
    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls <= 2:
            return AgentResponse(
                f"Read pass {self.calls}.",
                [ToolCall("call-1", "read_file", {"path": "target.py"})],
            )
        return AgentResponse("Two reads completed; validation not run.", [])


class OneWriteModel:
    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                "Applying one edit.",
                [
                    ToolCall(
                        "replace-1",
                        "replace_text",
                        {
                            "path": "target.py",
                            "old": "value = 1\n",
                            "new": "value = 2\n",
                        },
                    )
                ],
            )
        return AgentResponse("Edit complete; validation not run.", [])


class WriteThenReadModel:
    """首个状态变更失败收口时，后续 read 也必须得到未执行 Observation。"""

    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                "Apply the edit, then inspect the result.",
                [
                    ToolCall(
                        "replace-1",
                        "replace_text",
                        {
                            "path": "target.py",
                            "old": "value = 1\n",
                            "new": "value = 2\n",
                        },
                    ),
                    ToolCall("read-after", "read_file", {"path": "target.py"}),
                ],
            )
        return AgentResponse("Edit complete; validation not run.", [])


class CommandThenFinalModel:
    """同一 durable command ToolCall 在 crash resume 后只能产生一次副作用。"""

    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                "Run the governed validation command.",
                [
                    ToolCall(
                        "command-1",
                        "run_command",
                        {"command": "python -m pytest -q test_counter.py"},
                    )
                ],
            )
        return AgentResponse("Validation complete.", [])


class FailedCommandThenFinalModel:
    """失败 command 的 durable 结果在 crash resume 后也只能回放一次。"""

    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                "Run the governed failing validation command.",
                [
                    ToolCall(
                        "command-1",
                        "run_command",
                        {"command": "python -m pytest -q test_counter_fail.py"},
                    )
                ],
            )
        return AgentResponse("Validation failure was observed.", [])


class CrashBeforeExecutedCommit(JsonOperationLedgerRepository):
    """Gateway 已改变文件，但 executed transition 尚未 durable commit。"""

    def record_executed(self, update):
        raise RuntimeError("fault injection: crash before executed commit")


class CrashBeforePendingPointer(JsonTaskStateRepository):
    """assistant 已 append，但首个 pending pointer 尚未落盘。"""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.failed_once = False

    def update(self, checkpoint, update):
        if (
            not self.failed_once
            and isinstance(update.pending_execution, PendingExecutionPointer)
        ):
            self.failed_once = True
            raise RuntimeError("fault injection: crash before pending pointer")
        return super().update(checkpoint, update)


class CrashBeforeFinalCheckpoint(JsonTaskStateRepository):
    """accepted final 已 append，但 terminal checkpoint 尚未落盘。"""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.failed_once = False

    def update(self, checkpoint, update):
        if (
            not self.failed_once
            and update.status_value() == TaskRunStatus.COMPLETED.value
        ):
            self.failed_once = True
            raise RuntimeError("fault injection: crash before final checkpoint")
        return super().update(checkpoint, update)


class CrashBeforeHumanToolObservation(JsonConversationThreadRepository):
    """human authority item 已落盘，原 ask_human Observation 尚未落盘。"""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.failed_once = False

    def append(self, thread_id, item):
        if (
            not self.failed_once
            and item.role == "tool"
            and item.tool_call_id == "ask-1"
        ):
            self.failed_once = True
            raise RuntimeError("fault injection: crash before ask_human observation")
        return super().append(thread_id, item)


class CrashBeforeCommandObservation(JsonConversationThreadRepository):
    """command 已执行且 Ledger 已提交，但 canonical Observation 尚未落盘。"""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.failed_once = False

    def append(self, thread_id, item):
        if (
            not self.failed_once
            and item.role == "tool"
            and item.tool_call_id == "command-1"
        ):
            self.failed_once = True
            raise RuntimeError(
                "fault injection: crash after command, before observation"
            )
        return super().append(thread_id, item)


class OneFinalModel:
    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        return AgentResponse("Finished; validation not run.", [])


class CountingStopHook(RuntimeHook):
    """accepted final 已 durable 后，crash resume 不应重放 stop hook。"""

    name = "counting_stop_hook"

    def __init__(self) -> None:
        self.calls = 0

    def on_stop(self, run_id, reason, final_answer):
        self.calls += 1
        return HookDecision(
            hook_name=self.name,
            decision=HookDecisionType.ALLOW,
            reason="accepted",
        )


def _registry(root: Path, *, include_human: bool = False) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadFileTool(WorkspaceSandbox(root)))
    registry.register(
        ReplaceTextTool(WorkspaceSandbox(root), auto_approve_writes=True)
    )
    if include_human:
        registry.register(AskHumanTool())
    return registry


def _command_registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(RunCommandTool(WorkspaceSandbox(root)))
    return registry


def _config(root: Path, **overrides) -> HarnessConfig:
    values = {
        "workspace": str(root),
        "output_root": str(root / "runs"),
        "conversation_thread_root": str(root / "threads"),
        "approval_root": str(root / "approvals"),
        "human_input_root": str(root / "human-input"),
        "operation_ledger_root": str(root / "operations"),
        "tool_routing_mode": "all",
        "skill_mode": "none",
        "max_steps": 4,
    }
    values.update(overrides)
    return HarnessConfig(**values)


def _checkpoint_path(result) -> Path:
    return result.artifact_dir / "task_state" / f"{result.run_id}.json"


def _claim_direct_loop_run(
    repository: JsonConversationThreadRepository,
    task_states: JsonTaskStateRepository,
    *,
    thread_id: str,
    turn_id: str,
    expected_current_run_id: str,
    trace: TraceRecorder,
) -> None:
    """Direct AgentLoop fixture 也必须先取得 canonical current Run ownership。"""

    thread = repository.get(thread_id)
    assert thread is not None
    task_states.start(
        TaskStartRequest(
            run_id=trace.run_id,
            thread_id=thread_id,
            turn_id=turn_id,
            workspace=thread.workspace,
            execution_workspace=thread.workspace,
            execution_mode="local",
            agent_name="CodingAgent",
        )
    )
    repository.claim_resume_run(
        thread_id,
        turn_id,
        expected_current_run_id=expected_current_run_id,
        run=ThreadRun(
            run_id=trace.run_id,
            artifact_dir=str(Path(task_states.root).parent),
            checkpoint_path=str(task_states.path_for(trace.run_id)),
            status=TaskRunStatus.CREATED.value,
            relationship="resume",
            parent_run_id=expected_current_run_id,
            created_at=trace.started_at,
            updated_at=trace.started_at,
        ),
    )


class PendingToolBatchResumeTest(unittest.TestCase):
    def test_approval_resume_continues_original_batch_without_model_reproposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            model = ApprovalBatchModel()
            harness = Harness(
                model=model,
                tools=_registry(root),
                config=_config(
                    root,
                    auto_approve_writes=False,
                    approval_mode="on-write",
                ),
            )

            waiting = harness.run("inspect and update target")
            self.assertEqual(waiting.status, TaskRunStatus.WAITING_APPROVAL)
            self.assertEqual(model.calls, 1)
            pointer = waiting.checkpoint.pending_execution
            self.assertIsNotNone(pointer)
            assert pointer is not None
            self.assertEqual(pointer.next_tool_call_index, 1)
            self.assertTrue(pointer.pending_operation_key)

            thread_repo = JsonConversationThreadRepository(root / "threads")
            first_items = thread_repo.list_items(waiting.thread_id, turn_id=waiting.turn_id)
            assistant = next(item for item in first_items if item.origin == "model_tool_calls")
            self.assertEqual(
                assistant.content,
                "I inspected the target and will now apply the edit.",
            )
            self.assertEqual(len(assistant.tool_calls), 2)
            self.assertEqual(len([item for item in first_items if item.role == "tool"]), 1)

            approvals = JsonApprovalRepository(root / "approvals")
            pending_approval = approvals.list_pending()[0]
            approvals.decide(pending_approval.operation_key, "approved")
            completed = harness.resume(_checkpoint_path(waiting))

            self.assertEqual(completed.status, TaskRunStatus.COMPLETED)
            self.assertEqual(model.calls, 2)
            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"),
                "value = 2\n",
            )
            self.assertIsNone(completed.checkpoint.pending_execution)
            items = thread_repo.list_items(completed.thread_id, turn_id=completed.turn_id)
            tool_items = [item for item in items if item.role == "tool"]
            self.assertEqual(len(tool_items), 2)
            self.assertEqual(len({item.item_id for item in tool_items}), 2)

    def test_rejection_consumes_original_call_and_next_model_step_sees_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            model = RejectedWriteModel()
            harness = Harness(
                model=model,
                tools=_registry(root),
                config=_config(
                    root,
                    auto_approve_writes=False,
                    approval_mode="on-write",
                ),
            )
            waiting = harness.run("request one edit")
            approvals = JsonApprovalRepository(root / "approvals")
            approval = approvals.list_pending()[0]
            approvals.decide(approval.operation_key, "rejected")

            completed = harness.resume(_checkpoint_path(waiting))

            self.assertEqual(completed.status, TaskRunStatus.COMPLETED)
            self.assertEqual(model.calls, 2)
            self.assertTrue(model.saw_rejection)
            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"),
                "value = 1\n",
            )
            self.assertIsNone(completed.checkpoint.pending_execution)
            items = JsonConversationThreadRepository(root / "threads").list_items(
                completed.thread_id,
                turn_id=completed.turn_id,
            )
            rejected_items = [
                item
                for item in items
                if item.role == "tool" and "approval rejected" in item.content
            ]
            self.assertEqual(len(rejected_items), 1)
            self.assertEqual(rejected_items[0].tool_call_id, "replace-1")

    def test_ask_human_preserves_full_batch_and_authoritative_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            model = AskHumanBatchModel()
            harness = Harness(
                model=model,
                tools=_registry(root, include_human=True),
                config=_config(root),
            )
            waiting = harness.run("choose runtime before continuing")
            self.assertEqual(waiting.status, TaskRunStatus.WAITING_HUMAN)
            inputs = JsonHumanInputRepository(root / "human-input")
            request = inputs.list_pending()[0]
            inputs.respond(request.request_id, "3.11")

            completed = harness.resume(_checkpoint_path(waiting))

            self.assertEqual(completed.status, TaskRunStatus.COMPLETED)
            self.assertTrue(model.saw_preceding_context)
            items = JsonConversationThreadRepository(root / "threads").list_items(
                completed.thread_id,
                turn_id=completed.turn_id,
            )
            assistant = next(item for item in items if item.origin == "model_tool_calls")
            self.assertEqual(len(assistant.tool_calls), 3)
            self.assertEqual(len([item for item in items if item.role == "tool"]), 3)
            answers = [
                item
                for item in items
                if item.origin == "operator"
                and item.human_authority
                and item.item_id == f"human-input:{request.request_id}"
            ]
            self.assertEqual(len(answers), 1)
            self.assertIn("3.11", answers[0].content)

    def test_budget_skips_still_close_every_provider_tool_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            result = Harness(
                model=BudgetedReadModel(),
                tools=_registry(root),
                config=_config(root, max_tool_calls_per_turn=1),
            ).run("read target through bounded views")

            self.assertEqual(result.status, TaskRunStatus.COMPLETED)
            items = JsonConversationThreadRepository(root / "threads").list_items(
                result.thread_id,
                turn_id=result.turn_id,
            )
            assistant = next(item for item in items if item.origin == "model_tool_calls")
            tool_items = [item for item in items if item.role == "tool"]
            self.assertEqual(len(assistant.tool_calls), 3)
            self.assertEqual(len(tool_items), 3)
            self.assertEqual(
                sum("execution budget" in item.content for item in tool_items),
                2,
            )

    def test_ask_human_answer_append_crash_resumes_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            repository = CrashBeforeHumanToolObservation(root / "threads")
            model = AskHumanBatchModel()
            harness = Harness(
                model=model,
                tools=_registry(root, include_human=True),
                config=_config(root),
                extensions=HarnessExtensions(conversation_threads=repository),
            )
            waiting = harness.run("choose runtime before continuing")
            request = JsonHumanInputRepository(root / "human-input").list_pending()[0]
            JsonHumanInputRepository(root / "human-input").respond(
                request.request_id,
                "3.11",
            )
            checkpoint_path = _checkpoint_path(waiting)

            with self.assertRaisesRegex(
                RuntimeError,
                "crash before ask_human observation",
            ):
                harness.resume(checkpoint_path)

            crashed_items = repository.list_items(
                waiting.thread_id,
                turn_id=waiting.turn_id,
            )
            self.assertEqual(
                len(
                    [
                        item
                        for item in crashed_items
                        if item.item_id == f"human-input:{request.request_id}"
                    ]
                ),
                1,
            )

            self.assertFalse(
                any(
                    item.role == "tool" and item.tool_call_id == "ask-1"
                    for item in crashed_items
                )
            )

            crashed_thread = repository.get(waiting.thread_id)
            assert crashed_thread is not None
            crashed_run = crashed_thread.require_turn(waiting.turn_id).runs[-1]
            completed = harness.resume(crashed_run.checkpoint_path)
            self.assertEqual(completed.status, TaskRunStatus.COMPLETED)
            items = repository.list_items(
                completed.thread_id,
                turn_id=completed.turn_id,
            )
            self.assertEqual(
                len(
                    [
                        item
                        for item in items
                        if item.item_id == f"human-input:{request.request_id}"
                    ]
                ),
                1,
            )
            self.assertEqual(
                len(
                    [
                        item
                        for item in items
                        if item.role == "tool" and item.tool_call_id == "ask-1"
                    ]
                ),
                1,
            )

    def test_later_same_human_question_creates_a_new_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = RepeatedHumanQuestionModel()
            harness = Harness(
                model=model,
                tools=_registry(root, include_human=True),
                config=_config(root),
            )

            first_wait = harness.run("ask the operator twice")
            inputs = JsonHumanInputRepository(root / "human-input")
            first_request = inputs.list_pending()[0]
            inputs.respond(first_request.request_id, "3.11")

            second_wait = harness.resume(_checkpoint_path(first_wait))

            self.assertEqual(second_wait.status, TaskRunStatus.WAITING_HUMAN)
            self.assertEqual(model.calls, 2)
            second_request = inputs.list_pending()[0]
            self.assertNotEqual(second_request.request_id, first_request.request_id)
            self.assertEqual(second_request.question, first_request.question)
            self.assertNotEqual(
                second_request.invocation_id,
                first_request.invocation_id,
            )
            inputs.respond(second_request.request_id, "3.12")

            completed = harness.resume(_checkpoint_path(second_wait))

            self.assertEqual(completed.status, TaskRunStatus.COMPLETED)
            self.assertEqual(model.calls, 3)

    def test_same_provider_tool_call_id_in_two_model_steps_has_distinct_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            result = Harness(
                model=ReusedCallIdModel(),
                tools=_registry(root),
                config=_config(root, max_steps=3),
            ).run("read target twice")

            self.assertEqual(result.status, TaskRunStatus.COMPLETED)
            items = JsonConversationThreadRepository(root / "threads").list_items(
                result.thread_id,
                turn_id=result.turn_id,
            )
            repeated = [
                item
                for item in items
                if item.role == "tool" and item.tool_call_id == "call-1"
            ]
            self.assertEqual(len(repeated), 2)
            self.assertEqual(len({item.item_id for item in repeated}), 2)

    def test_crash_after_state_change_resumes_as_outcome_unknown_without_reexecution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.py"
            target.write_text("value = 1\n", encoding="utf-8")
            model = WriteThenReadModel()
            operation_repository = CrashBeforeExecutedCommit(root / "operations")
            harness = Harness(
                model=model,
                tools=_registry(root),
                config=_config(root),
                extensions=HarnessExtensions(
                    operation_repository=operation_repository,
                ),
            )

            with self.assertRaisesRegex(RuntimeError, "crash before executed commit"):
                harness.run("apply one durable edit")
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 2\n")
            self.assertEqual(model.calls, 1)
            checkpoint_path = next((root / "runs").rglob("task_state/*.json"))

            blocked = harness.resume(checkpoint_path)

            self.assertEqual(blocked.status, TaskRunStatus.BLOCKED)
            self.assertEqual(blocked.stop_reason, "operation_outcome_unknown")
            self.assertEqual(model.calls, 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 2\n")
            items = JsonConversationThreadRepository(root / "threads").list_items(
                blocked.thread_id,
                turn_id=blocked.turn_id,
            )
            unknown = [
                item
                for item in items
                if item.role == "tool"
                and item.tool_call_id == "replace-1"
                and "operation_outcome_unknown" in item.content
            ]
            self.assertEqual(len(unknown), 1)
            tool_items = [item for item in items if item.role == "tool"]
            self.assertEqual(len(tool_items), 2)
            self.assertEqual(
                len(
                    [
                        item
                        for item in tool_items
                        if "terminated by operation_outcome_unknown" in item.content
                    ]
                ),
                1,
            )

    def test_crash_after_command_execution_replays_ledger_without_reexecution(self):
        """命令完成后到 Observation 落盘前崩溃，恢复不得再次运行同一 ToolCall。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test_counter.py").write_text(
                """from pathlib import Path

counter = Path("counter.txt")
counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else "1")

def test_ok():
    assert True
""",
                encoding="utf-8",
            )
            repository = CrashBeforeCommandObservation(root / "threads")
            model = CommandThenFinalModel()
            harness = Harness(
                model=model,
                tools=_command_registry(root),
                config=_config(root),
                extensions=HarnessExtensions(
                    conversation_threads=repository,
                ),
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "crash after command, before observation",
            ):
                harness.run("run one validation command")

            self.assertEqual((root / "counter.txt").read_text(), "1")
            checkpoint_path = next((root / "runs").rglob("task_state/*.json"))

            completed = harness.resume(checkpoint_path)

            self.assertEqual(completed.status, TaskRunStatus.COMPLETED)
            self.assertEqual(model.calls, 2)
            self.assertEqual((root / "counter.txt").read_text(), "1")
            items = repository.list_items(
                completed.thread_id,
                turn_id=completed.turn_id,
            )
            observations = [
                item
                for item in items
                if item.role == "tool" and item.tool_call_id == "command-1"
            ]
            self.assertEqual(len(observations), 1)
            self.assertIn("exit_code=0", observations[0].content)

    def test_crash_after_failed_command_replays_failure_without_reexecution(self):
        """失败结果已进 Ledger 时，Observation 崩溃窗口也不得重跑同一命令。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test_counter_fail.py").write_text(
                """from pathlib import Path

counter = Path("counter.txt")
counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else "1")

def test_failure():
    assert False
""",
                encoding="utf-8",
            )
            repository = CrashBeforeCommandObservation(root / "threads")
            model = FailedCommandThenFinalModel()
            harness = Harness(
                model=model,
                tools=_command_registry(root),
                config=_config(root),
                extensions=HarnessExtensions(
                    conversation_threads=repository,
                ),
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "crash after command, before observation",
            ):
                harness.run("run one failing validation command")

            self.assertEqual((root / "counter.txt").read_text(), "1")
            checkpoint_path = next((root / "runs").rglob("task_state/*.json"))

            completed = harness.resume(checkpoint_path)

            self.assertEqual(completed.status, TaskRunStatus.COMPLETED)
            self.assertEqual(model.calls, 2)
            self.assertEqual((root / "counter.txt").read_text(), "1")
            operation = next((root / "operations").glob("*.json"))
            operation_record = JsonOperationLedgerRepository(
                root / "operations"
            ).get(operation.stem)
            self.assertIsNotNone(operation_record)
            assert operation_record is not None
            self.assertEqual(
                operation_record.status,
                "failed",
            )
            observations = [
                item
                for item in repository.list_items(
                    completed.thread_id,
                    turn_id=completed.turn_id,
                )
                if item.role == "tool" and item.tool_call_id == "command-1"
            ]
            self.assertEqual(len(observations), 1)
            self.assertIn("replayed failed operation", observations[0].content)
            self.assertIn("exit_code=1", observations[0].content)

    def test_orphaned_assistant_batch_repairs_pointer_without_model_reproposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.py"
            target.write_text("value = 1\n", encoding="utf-8")
            thread_id = "thread-orphan"
            turn_id = "turn-orphan"
            thread_repository = JsonConversationThreadRepository(root / "threads")
            thread_repository.create(
                ConversationThread(
                    thread_id=thread_id,
                    title="orphan recovery",
                    initial_task="apply one durable edit",
                    workspace=str(root),
                    created_at=1.0,
                    updated_at=1.0,
                )
            )
            task_states = CrashBeforePendingPointer(root / "task-state")
            task_states.start(
                TaskStartRequest(
                    run_id="bootstrap",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    workspace=str(root),
                    execution_workspace=str(root),
                    execution_mode="local",
                    agent_name="CodingAgent",
                )
            )
            thread_repository.start_turn(
                thread_id,
                Turn(
                    turn_id=turn_id,
                    root_task="apply one durable edit",
                    input_item_id=f"user:{turn_id}",
                    status="active",
                    created_at=1.0,
                    updated_at=1.0,
                ),
                ConversationItemDraft(
                    item_id=f"user:{turn_id}",
                    turn_id=turn_id,
                    run_id="bootstrap",
                    role="user",
                    content="apply one durable edit",
                    origin="human",
                    human_authority=True,
                ),
                ThreadRun(
                    run_id="bootstrap",
                    artifact_dir=str(root / "bootstrap-artifacts"),
                    checkpoint_path=str(root / "task-state" / "bootstrap.json"),
                    status="created",
                    relationship="initial",
                    created_at=1.0,
                    updated_at=1.0,
                ),
            )
            model = OneWriteModel()
            first_trace = TraceRecorder(str(root / "first-trace.json"))
            common_config = dict(
                workspace=str(root),
                requested_workspace=str(root),
                thread_id=thread_id,
                turn_id=turn_id,
                conversation_thread_root=str(root / "threads"),
                task_state_root=str(root / "task-state"),
                approval_root=str(root / "approvals"),
                human_input_root=str(root / "human-input"),
                operation_ledger_root=str(root / "operations"),
                memory_root=str(root / "memory"),
                tool_routing_mode="all",
                skill_mode="none",
                max_steps=3,
            )
            first_config = RuntimeConfig(**common_config)
            _claim_direct_loop_run(
                thread_repository,
                task_states,
                thread_id=thread_id,
                turn_id=turn_id,
                expected_current_run_id="bootstrap",
                trace=first_trace,
            )
            first_loop = build_agent_loop_from_request(
                AgentLoopBuildRequest(
                    config=first_config,
                    trace=first_trace,
                    registry=_registry(root),
                    llm=model,
                    overrides=RuntimeDependencyOverrides(
                        task_states=task_states,
                        conversation_threads=thread_repository,
                    ),
                )
            )

            with self.assertRaisesRegex(RuntimeError, "crash before pending pointer"):
                first_loop.run()
            self.assertEqual(model.calls, 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\n")
            first_checkpoint = task_states.path_for(first_trace.run_id)
            assistant_items = [
                item
                for item in thread_repository.list_items(thread_id, turn_id=turn_id)
                if item.origin == "model_tool_calls"
            ]
            self.assertEqual(len(assistant_items), 1)

            second_trace = TraceRecorder(str(root / "second-trace.json"))
            second_config = RuntimeConfig(
                **common_config,
                resume_state=str(first_checkpoint),
            )
            _claim_direct_loop_run(
                thread_repository,
                task_states,
                thread_id=thread_id,
                turn_id=turn_id,
                expected_current_run_id=first_trace.run_id,
                trace=second_trace,
            )
            second_loop = build_agent_loop_from_request(
                AgentLoopBuildRequest(
                    config=second_config,
                    trace=second_trace,
                    registry=_registry(root),
                    llm=model,
                    overrides=RuntimeDependencyOverrides(
                        task_states=task_states,
                        conversation_threads=thread_repository,
                    ),
                )
            )
            final = second_loop.run()

            self.assertIn("Edit complete", final)
            self.assertEqual(model.calls, 2)
            self.assertEqual(target.read_text(encoding="utf-8"), "value = 2\n")
            items = thread_repository.list_items(thread_id, turn_id=turn_id)
            self.assertEqual(
                len([item for item in items if item.origin == "model_tool_calls"]),
                1,
            )
            self.assertEqual(len([item for item in items if item.role == "tool"]), 1)

    def test_durable_final_is_idempotently_closed_without_second_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            thread_id = "thread-final"
            turn_id = "turn-final"
            thread_repository = JsonConversationThreadRepository(root / "threads")
            thread_repository.create(
                ConversationThread(
                    thread_id=thread_id,
                    title="final recovery",
                    initial_task="finish once",
                    workspace=str(root),
                    created_at=1.0,
                    updated_at=1.0,
                )
            )
            task_states = CrashBeforeFinalCheckpoint(root / "task-state")
            task_states.start(
                TaskStartRequest(
                    run_id="bootstrap",
                    thread_id=thread_id,
                    turn_id=turn_id,
                    workspace=str(root),
                    execution_workspace=str(root),
                    execution_mode="local",
                    agent_name="CodingAgent",
                )
            )
            thread_repository.start_turn(
                thread_id,
                Turn(
                    turn_id=turn_id,
                    root_task="finish once",
                    input_item_id=f"user:{turn_id}",
                    status="active",
                    created_at=1.0,
                    updated_at=1.0,
                ),
                ConversationItemDraft(
                    item_id=f"user:{turn_id}",
                    turn_id=turn_id,
                    run_id="bootstrap",
                    role="user",
                    content="finish once",
                    origin="human",
                    human_authority=True,
                ),
                ThreadRun(
                    run_id="bootstrap",
                    artifact_dir=str(root / "bootstrap-artifacts"),
                    checkpoint_path=str(root / "task-state" / "bootstrap.json"),
                    status="created",
                    relationship="initial",
                    created_at=1.0,
                    updated_at=1.0,
                ),
            )
            model = OneFinalModel()
            stop_hook = CountingStopHook()
            common_config = dict(
                workspace=str(root),
                requested_workspace=str(root),
                thread_id=thread_id,
                turn_id=turn_id,
                conversation_thread_root=str(root / "threads"),
                task_state_root=str(root / "task-state"),
                approval_root=str(root / "approvals"),
                human_input_root=str(root / "human-input"),
                operation_ledger_root=str(root / "operations"),
                memory_root=str(root / "memory"),
                tool_routing_mode="all",
                skill_mode="none",
                max_steps=2,
            )
            first_trace = TraceRecorder(str(root / "first-final-trace.json"))
            _claim_direct_loop_run(
                thread_repository,
                task_states,
                thread_id=thread_id,
                turn_id=turn_id,
                expected_current_run_id="bootstrap",
                trace=first_trace,
            )
            first_loop = build_agent_loop_from_request(
                AgentLoopBuildRequest(
                    config=RuntimeConfig(**common_config),
                    trace=first_trace,
                    registry=_registry(root),
                    llm=model,
                    overrides=RuntimeDependencyOverrides(
                        task_states=task_states,
                        conversation_threads=thread_repository,
                        additional_hooks=(stop_hook,),
                    ),
                )
            )
            with self.assertRaisesRegex(RuntimeError, "crash before final checkpoint"):
                first_loop.run()
            self.assertEqual(model.calls, 1)
            self.assertEqual(stop_hook.calls, 1)
            first_checkpoint = task_states.path_for(first_trace.run_id)
            self.assertEqual(
                len(
                    [
                        item
                        for item in thread_repository.list_items(
                            thread_id,
                            turn_id=turn_id,
                        )
                        if item.origin == "model_final"
                    ]
                ),
                1,
            )

            second_trace = TraceRecorder(str(root / "second-final-trace.json"))
            _claim_direct_loop_run(
                thread_repository,
                task_states,
                thread_id=thread_id,
                turn_id=turn_id,
                expected_current_run_id=first_trace.run_id,
                trace=second_trace,
            )
            second_loop = build_agent_loop_from_request(
                AgentLoopBuildRequest(
                    config=RuntimeConfig(
                        **common_config,
                        resume_state=str(first_checkpoint),
                    ),
                    trace=second_trace,
                    registry=_registry(root),
                    llm=model,
                    overrides=RuntimeDependencyOverrides(
                        task_states=task_states,
                        conversation_threads=thread_repository,
                        additional_hooks=(stop_hook,),
                    ),
                )
            )
            final = second_loop.run()

            self.assertEqual(final, "Finished; validation not run.")
            self.assertEqual(model.calls, 1)
            self.assertEqual(stop_hook.calls, 1)
            checkpoint = task_states.load(second_trace.run_id)
            self.assertEqual(checkpoint.status, TaskRunStatus.COMPLETED.value)
            self.assertEqual(checkpoint.final_answer, final)


if __name__ == "__main__":
    unittest.main()

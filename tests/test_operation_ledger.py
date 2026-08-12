import tempfile
import unittest
from pathlib import Path

from agent_forge.observability.api import TraceRecorder
from agent_forge.runtime.adapters import (
    JsonApprovalRepository,
    JsonOperationLedgerRepository,
)
from agent_forge.runtime.api import build_agent_loop
from agent_forge.runtime.application.operation_tracker import OperationTracker
from agent_forge.runtime.wiring import (
    AgentLoopBuildRequest,
    RuntimeDependencyOverrides,
    build_agent_loop_from_request,
)
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.runtime.domain.conversation import ToolCall
from agent_forge.runtime.domain.operation import (
    OperationPlan,
    OperationTarget,
    OperationTransition,
)
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.create_file import CreateFileTool
from agent_forge.tools.replace_text import ReplaceTextTool
from agent_forge.tools.registry import ToolRegistry


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
        return AgentResponse("PASS\nfinished", [])


class ApplyRestoreReapplyThenFinalLLM:
    """先应用、再恢复、最后重放同一写操作的通用状态机。"""

    last_usage = None

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.calls in {1, 3}:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        f"apply-{self.calls}",
                        "replace_text",
                        {
                            "path": "target.py",
                            "old": "value = 1\n",
                            "new": "value = 2\n",
                        },
                    )
                ],
            )
        if self.calls == 2:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "restore-2",
                        "replace_text",
                        {
                            "path": "target.py",
                            "old": "value = 2\n",
                            "new": "value = 1\n",
                        },
                    )
                ],
            )
        return AgentResponse("PASS\nfinished", [])


class ReplaceSequenceThenFinalLLM:
    """依次执行通用文本状态转换，序列耗尽后结束。"""

    last_usage = None

    def __init__(self, replacements):
        self.replacements = replacements
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        index = self.calls - 1
        if index < len(self.replacements):
            old, new = self.replacements[index]
            return AgentResponse(
                None,
                [
                    ToolCall(
                        f"replace-{self.calls}",
                        "replace_text",
                        {"path": "target.py", "old": old, "new": new},
                    )
                ],
            )
        return AgentResponse("PASS\nfinished", [])


class CreateThenFinalLLM:
    """首轮创建文件，次轮结束；用于验证完整持久状态变更链。"""

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
                        "create-1",
                        "create_file",
                        {"path": "generated.py", "content": "value = 1\n"},
                    )
                ],
            )
        return AgentResponse("PASS\nfile created", [])


class CrashBeforeLedgerCommit(JsonOperationLedgerRepository):
    """故障注入：文件已改变，但最终 executed 事实尚未提交时进程崩溃。"""

    def record_executed(self, update):
        raise RuntimeError("fault injection: crash before executed commit")


def _registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReplaceTextTool(WorkspaceSandbox(root), auto_approve_writes=True))
    return registry


def _create_registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        CreateFileTool(WorkspaceSandbox(root), auto_approve_writes=True)
    )
    return registry


class OperationLedgerTest(unittest.TestCase):
    def _run_replace_sequence(self, root, replacements, *, trace_name, max_steps):
        trace = TraceRecorder(str(root / trace_name))
        result = build_agent_loop(
            RuntimeConfig(
                workspace=str(root),
                max_steps=max_steps,
                trace_file=str(root / trace_name),
                operation_ledger_root=str(root / "ledger"),
            ),
            trace,
            _registry(root),
            ReplaceSequenceThenFinalLLM(replacements),
        ).run("apply the requested generic state transitions")
        return result, trace

    def test_same_run_replays_operation_after_inverse_restores_precondition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            ledger_root = root / "ledger"
            trace = TraceRecorder(str(root / "trace.json"))
            config = RuntimeConfig(
                workspace=str(root),
                max_steps=4,
                trace_file=str(root / "trace.json"),
                operation_ledger_root=str(ledger_root),
            )

            result = build_agent_loop(
                config,
                trace,
                _registry(root),
                ApplyRestoreReapplyThenFinalLLM(),
            ).run("apply, verify by restoring, then reapply")

            self.assertIn("finished", result)
            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"),
                "value = 2\n",
            )
            markers = [
                event
                for event in trace.events
                if event["event_type"] == "operation_ledger"
                and event.get("operation_status")
                == "replay_authorized_restored_precondition"
            ]
            self.assertEqual(len(markers), 1)
            marker = markers[0]
            operation = marker["operation"]
            self.assertEqual(operation["run_id"], marker["run_id"])
            self.assertEqual(
                marker["current_fingerprint"], operation["pre_fingerprint"]
            )
            self.assertNotEqual(
                marker["current_fingerprint"], operation["post_fingerprint"]
            )
            self.assertEqual(operation["history"].count("executed"), 1)

            marker_index = trace.events.index(marker)
            same_key_events = [
                (index, event)
                for index, event in enumerate(trace.events[marker_index + 1 :], marker_index + 1)
                if event["event_type"] == "operation_ledger"
                and event.get("operation_key") == marker["operation_key"]
            ]
            self.assertGreaterEqual(len(same_key_events), 2)
            executing_index, executing = same_key_events[0]
            executed_index, executed = same_key_events[1]
            self.assertEqual(executing["operation_status"], "executing")
            self.assertEqual(executed["operation_status"], "executed")
            self.assertEqual(executing["step"], marker["step"])
            self.assertEqual(executed["step"], marker["step"])
            self.assertEqual(
                sum(
                    event["event_type"] == "tool_execution_started"
                    for event in trace.events[executing_index:executed_index]
                ),
                1,
            )
            self.assertEqual(
                executed["operation"]["post_fingerprint"],
                operation["post_fingerprint"],
            )

            record = JsonOperationLedgerRepository(ledger_root).get(
                marker["operation_key"]
            )
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.history.count("executed"), 2)

    def test_same_run_duplicate_at_post_state_remains_idempotently_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")

            result, trace = self._run_replace_sequence(
                root,
                [
                    ("value = 1\n", "value = 2\n"),
                    ("value = 1\n", "value = 2\n"),
                ],
                trace_name="same-post.json",
                max_steps=3,
            )

            self.assertIn("finished", result)
            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"),
                "value = 2\n",
            )
            statuses = [
                event.get("operation_status")
                for event in trace.events
                if event["event_type"] == "operation_ledger"
            ]
            self.assertIn("skipped_already_executed", statuses)
            self.assertNotIn("replay_authorized_restored_precondition", statuses)
            self.assertEqual(
                sum(
                    event["event_type"] == "tool_execution_started"
                    for event in trace.events
                ),
                1,
            )

    def test_same_run_unrecognized_drift_remains_stale_and_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")

            result, trace = self._run_replace_sequence(
                root,
                [
                    ("value = 1\n", "value = 2\n"),
                    ("value = 2\n", "value = 3\n"),
                    ("value = 1\n", "value = 2\n"),
                ],
                trace_name="same-drift.json",
                max_steps=4,
            )

            self.assertIn("blocked: executed operation target has changed", result)
            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"),
                "value = 3\n",
            )
            statuses = [
                event.get("operation_status")
                for event in trace.events
                if event["event_type"] == "operation_ledger"
            ]
            self.assertIn("stale_operation_record", statuses)
            self.assertNotIn("replay_authorized_restored_precondition", statuses)

    def test_cross_run_restore_to_precondition_remains_stale_and_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")

            first, _ = self._run_replace_sequence(
                root,
                [("value = 1\n", "value = 2\n")],
                trace_name="first-run.json",
                max_steps=2,
            )
            self.assertIn("finished", first)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")

            second, trace = self._run_replace_sequence(
                root,
                [("value = 1\n", "value = 2\n")],
                trace_name="second-run.json",
                max_steps=2,
            )

            self.assertIn("blocked: executed operation target has changed", second)
            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"),
                "value = 1\n",
            )
            statuses = [
                event.get("operation_status")
                for event in trace.events
                if event["event_type"] == "operation_ledger"
            ]
            self.assertIn("stale_operation_record", statuses)
            self.assertNotIn("replay_authorized_restored_precondition", statuses)

    def test_third_apply_after_one_shot_replay_remains_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")

            result, trace = self._run_replace_sequence(
                root,
                [
                    ("value = 1\n", "value = 2\n"),
                    ("value = 2\n", "value = 1\n"),
                    ("value = 1\n", "value = 2\n"),
                    ("value = 2\n", "value = 1\n"),
                    ("value = 1\n", "value = 2\n"),
                ],
                trace_name="bounded-replay.json",
                max_steps=6,
            )

            self.assertIn("blocked: executed operation target has changed", result)
            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"),
                "value = 1\n",
            )
            statuses = [
                (event.get("operation_key"), event.get("operation_status"))
                for event in trace.events
                if event["event_type"] == "operation_ledger"
            ]
            marker_keys = [
                key
                for key, status in statuses
                if status == "replay_authorized_restored_precondition"
            ]
            self.assertEqual(len(marker_keys), 2)
            self.assertEqual(len(set(marker_keys)), 2)
            self.assertTrue(all(marker_keys.count(key) == 1 for key in marker_keys))
            stale_keys = [
                key for key, status in statuses if status == "stale_operation_record"
            ]
            self.assertEqual(stale_keys, [marker_keys[0]])
            records = [
                JsonOperationLedgerRepository(root / "ledger").get(path.stem)
                for path in (root / "ledger").glob("*.json")
            ]
            self.assertTrue(
                records
                and all(
                    record is not None
                    and record.history.count("executed") <= 2
                    for record in records
                )
            )

    def test_incomplete_fingerprint_is_not_replayable(self):
        self.assertFalse(OperationTracker._is_complete_fingerprint(None))
        self.assertFalse(
            OperationTracker._is_complete_fingerprint({"kind": "path"})
        )
        self.assertFalse(
            OperationTracker._is_complete_fingerprint(
                {
                    "kind": "path",
                    "tool_name": "replace_text",
                    "action": "write",
                    "path": "target.py",
                    "resolved_path": "/outside/target.py",
                    "inside_workspace": False,
                    "exists": True,
                    "sha256": "abc",
                    "size": 3,
                }
            )
        )

    def test_create_file_passes_approval_ledger_and_evidence_chain(self):
        """证明 create_file 会真实改变文件，而不只是写工具名单中的字符串。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger_root = root / "ledger"
            approval_root = root / "approvals"
            approvals = JsonApprovalRepository(approval_root)
            first_trace = TraceRecorder(str(root / "first-trace.json"))
            first_config = RuntimeConfig(
                workspace=str(root),
                max_steps=2,
                trace_file=str(root / "first-trace.json"),
                auto_approve_writes=False,
                approval_root=str(approval_root),
                operation_ledger_root=str(ledger_root),
            )

            waiting = build_agent_loop(
                first_config,
                first_trace,
                _create_registry(root),
                CreateThenFinalLLM(),
            ).run("create generated.py")

            self.assertIn("waiting_approval", waiting)
            self.assertFalse((root / "generated.py").exists())
            pending_approvals = approvals.list_pending()
            self.assertEqual(len(pending_approvals), 1)
            self.assertEqual(pending_approvals[0].tool_name, "create_file")

            ledger_files = list(ledger_root.glob("*.json"))
            self.assertEqual(len(ledger_files), 1)
            pending_operation = JsonOperationLedgerRepository(ledger_root).get(
                ledger_files[0].stem
            )
            self.assertIsNotNone(pending_operation)
            assert pending_operation is not None
            self.assertEqual(pending_operation.action, "write")
            self.assertEqual(pending_operation.history, ["planned", "pending"])
            self.assertFalse(
                any(
                    event.get("operation_status")
                    == "replay_authorized_restored_precondition"
                    for event in first_trace.events
                )
            )

            approvals.decide(pending_approvals[0].operation_key, "approved")
            second_trace = TraceRecorder(str(root / "second-trace.json"))
            second_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "second-trace.json"),
                auto_approve_writes=False,
                approval_root=str(approval_root),
                operation_ledger_root=str(ledger_root),
            )
            final = build_agent_loop(
                second_config,
                second_trace,
                _create_registry(root),
                CreateThenFinalLLM(),
            ).run("create generated.py")

            self.assertIn("file created", final)
            self.assertEqual(
                (root / "generated.py").read_text(encoding="utf-8"),
                "value = 1\n",
            )
            operation = JsonOperationLedgerRepository(ledger_root).get(
                ledger_files[0].stem
            )
            self.assertIsNotNone(operation)
            assert operation is not None
            self.assertEqual(operation.tool_name, "create_file")
            self.assertEqual(operation.action, "write")
            self.assertEqual(
                operation.history,
                ["planned", "pending", "approved", "executing", "executed"],
            )
            self.assertTrue(
                any(
                    event["event_type"] == "human_approval"
                    and event.get("observation") == "approved"
                    for event in second_trace.events
                )
            )
            self.assertTrue(
                any(
                    event["event_type"] == "evidence_collected"
                    and "create_file" in str(event.get("evidence"))
                    for event in second_trace.events
                )
            )

    def test_failed_operation_never_activates_restored_precondition_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")

            result, trace = self._run_replace_sequence(
                root,
                [
                    ("missing = 0\n", "value = 2\n"),
                    ("missing = 0\n", "value = 2\n"),
                ],
                trace_name="failed-operation.json",
                max_steps=3,
            )

            self.assertIn("finished", result)
            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"),
                "value = 1\n",
            )
            self.assertFalse(
                any(
                    event.get("operation_status")
                    == "replay_authorized_restored_precondition"
                    for event in trace.events
                )
            )
            records = [
                JsonOperationLedgerRepository(root / "ledger").get(path.stem)
                for path in (root / "ledger").glob("*.json")
            ]
            self.assertEqual(len(records), 1)
            self.assertIsNotNone(records[0])
            assert records[0] is not None
            self.assertEqual(records[0].status, "failed")
            self.assertEqual(records[0].history.count("executed"), 0)

    def test_store_records_pending_approved_and_executed_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonOperationLedgerRepository(Path(tmp) / "ledger")
            target = OperationTarget(
                tool_name="replace_text",
                arguments={"path": "target.py", "old": "a", "new": "b"},
                workspace=tmp,
                action="write",
            )
            key = JsonOperationLedgerRepository.operation_key(target)

            store.record_pending(
                OperationPlan(
                    operation_key=key,
                    target=target,
                    status="pending",
                    run_id="r1",
                    step=1,
                )
            )
            store.record_approved(
                OperationTransition(
                    operation_key=key,
                    status="approved",
                    run_id="r1",
                    step=1,
                )
            )
            store.record_executing(
                OperationTransition(
                    operation_key=key,
                    status="executing",
                    run_id="r1",
                    step=2,
                )
            )
            store.record_executed(
                OperationTransition(
                    operation_key=key,
                    status="executed",
                    run_id="r1",
                    step=2,
                    observation="replaced text once",
                )
            )

            record = store.get(key)
            self.assertEqual(record.status, "executed")
            self.assertEqual(
                record.history[-4:],
                ["pending", "approved", "executing", "executed"],
            )

    def test_agent_loop_skips_already_executed_side_effect_on_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            ledger_root = root / "ledger"

            first_trace = TraceRecorder(str(root / "first-trace.json"))
            first_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "first-trace.json"),
                operation_ledger_root=str(ledger_root),
            )
            build_agent_loop(
                first_config, first_trace, _registry(root), ReplaceThenFinalLLM()
            ).run("fix target")

            second_trace = TraceRecorder(str(root / "second-trace.json"))
            second_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "second-trace.json"),
                operation_ledger_root=str(ledger_root),
            )
            second = build_agent_loop(
                second_config, second_trace, _registry(root), ReplaceThenFinalLLM()
            ).run("fix target")

            self.assertIn("finished", second)
            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"), "value = 2\n"
            )
            self.assertTrue(
                any(
                    event["event_type"] == "operation_ledger"
                    and event.get("operation_status") == "skipped_already_executed"
                    for event in second_trace.events
                )
            )
            replayed_observations = [
                event.get("observation", "")
                for event in second_trace.events
                if event["event_type"] == "tool_observation"
            ]
            self.assertTrue(
                any(
                    "previous_observation=" in observation
                    for observation in replayed_observations
                )
            )
            self.assertFalse(
                any(
                    event["event_type"] == "tool_execution_started"
                    for event in second_trace.events
                )
            )

    def test_resume_does_not_repeat_side_effect_when_previous_outcome_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            ledger_root = root / "ledger"
            first_trace = TraceRecorder(str(root / "first-trace.json"))
            first_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "first-trace.json"),
                operation_ledger_root=str(ledger_root),
            )
            crashing_ledger = CrashBeforeLedgerCommit(ledger_root)

            with self.assertRaisesRegex(RuntimeError, "crash before executed commit"):
                build_agent_loop_from_request(
                    AgentLoopBuildRequest(
                        config=first_config,
                        trace=first_trace,
                        registry=_registry(root),
                        llm=ReplaceThenFinalLLM(),
                        overrides=RuntimeDependencyOverrides(
                            operations=crashing_ledger
                        ),
                    )
                ).run("fix target")

            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"),
                "value = 2\n",
            )
            records = list(ledger_root.glob("*.json"))
            self.assertEqual(len(records), 1)
            self.assertEqual(
                JsonOperationLedgerRepository(ledger_root).get(records[0].stem).status,
                "executing",
            )

            second_trace = TraceRecorder(str(root / "second-trace.json"))
            second_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "second-trace.json"),
                operation_ledger_root=str(ledger_root),
            )
            second = build_agent_loop(
                second_config,
                second_trace,
                _registry(root),
                ReplaceThenFinalLLM(),
            ).run("fix target")

            self.assertIn("blocked: operation outcome is unknown", second)
            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"),
                "value = 2\n",
            )
            self.assertTrue(
                any(
                    event["event_type"] == "operation_ledger"
                    and event.get("operation_status") == "operation_outcome_unknown"
                    for event in second_trace.events
                )
            )

    def test_agent_loop_blocks_stale_executed_operation_when_target_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            ledger_root = root / "ledger"

            first_trace = TraceRecorder(str(root / "first-trace.json"))
            first_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "first-trace.json"),
                operation_ledger_root=str(ledger_root),
            )
            build_agent_loop(
                first_config, first_trace, _registry(root), ReplaceThenFinalLLM()
            ).run("fix target")

            (root / "target.py").write_text("value = 3\n", encoding="utf-8")

            second_trace = TraceRecorder(str(root / "second-trace.json"))
            second_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "second-trace.json"),
                operation_ledger_root=str(ledger_root),
            )
            second = build_agent_loop(
                second_config, second_trace, _registry(root), ReplaceThenFinalLLM()
            ).run("fix target")

            self.assertIn("blocked: executed operation target has changed", second)
            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"), "value = 3\n"
            )
            self.assertTrue(
                any(
                    event["event_type"] == "operation_ledger"
                    and event.get("operation_status") == "stale_operation_record"
                    for event in second_trace.events
                )
            )


if __name__ == "__main__":
    unittest.main()

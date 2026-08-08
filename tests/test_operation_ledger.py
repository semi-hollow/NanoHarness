import tempfile
import unittest
from pathlib import Path

from agent_forge.observability.api import TraceRecorder
from agent_forge.runtime.adapters import (
    JsonApprovalRepository,
    JsonOperationLedgerRepository,
)
from agent_forge.runtime.api import build_agent_loop
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


class CreateThenFinalLLM:
    """首轮创建文件，次轮结束；用于验证完整写副作用链。"""

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
    """故障注入：副作用已发生，但最终 executed 事实尚未提交时进程崩溃。"""

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
    def test_create_file_passes_approval_ledger_and_evidence_chain(self):
        """证明 create_file 是真实副作用行为，不只是写工具名单中的字符串。"""

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

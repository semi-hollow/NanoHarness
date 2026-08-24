import tempfile
import unittest
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent_forge.harness import Harness
from agent_forge.harness_contracts import HarnessConfig
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
from agent_forge.runtime.adapters.openai_compatible import AgentResponse
from agent_forge.runtime.domain.conversation import ToolCall
from agent_forge.runtime.domain.operation import (
    OperationPlan,
    OperationTarget,
    OperationTransition,
)
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.builtins.create_file import CreateFileTool
from agent_forge.tools.builtins.replace_text import ReplaceTextTool
from agent_forge.tools.builtins.run_command import RunCommandTool
from agent_forge.tools.registry import ToolRegistry
from tests.support import (
    bind_follow_up_runtime_turn,
    bind_new_runtime_turn,
    bind_resume_runtime_turn,
)


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


class RunCommandThenFinalLLM:
    """每次 Run 执行同一验证命令，再结束。"""

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
                        "validate-1",
                        "run_command",
                        {"command": "python -m compileall target.py"},
                    )
                ],
            )
        return AgentResponse("PASS\nvalidation finished", [])


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


def _run_command_registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(RunCommandTool(WorkspaceSandbox(root)))
    return registry


class OperationLedgerTest(unittest.TestCase):
    def test_same_validation_command_executes_again_after_workspace_changes(self):
        """不同 canonical ToolCall 的相同命令不得回放修改代码前的 PASS。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger_root = root / "ledger"
            target = root / "target.py"
            target.write_text("value = 1\n", encoding="utf-8")

            trace_events = []
            for run_number in (1, 2):
                result = Harness(
                    model=RunCommandThenFinalLLM(),
                    tools=_run_command_registry(root),
                    config=HarnessConfig(
                        workspace=str(root),
                        output_root=str(root / f"runs-{run_number}"),
                        operation_ledger_root=str(ledger_root),
                        tool_routing_mode="all",
                        skill_mode="none",
                        max_steps=3,
                    ),
                ).run("validate target.py")
                trace_events.extend(
                    json.loads(result.trace_path.read_text(encoding="utf-8"))["events"]
                )
                self.assertIn("validation finished", result.final_answer or "")
                if run_number == 1:
                    target.write_text("value = 2\n", encoding="utf-8")

            self.assertEqual(
                sum(
                    event["event_type"] == "tool_execution_started"
                    for event in trace_events
                ),
                2,
            )
            self.assertEqual(len(list(ledger_root.glob("*.json"))), 2)
            self.assertEqual(
                sum(
                    event["event_type"] == "operation_ledger"
                    and event.get("operation_status") == "executed"
                    for event in trace_events
                ),
                2,
            )

    def test_on_risk_command_uses_same_invocation_key_for_approval_and_ledger(self):
        """同一 pending command 的 Approval 与 Ledger 共享调用级稳定身份。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            ledger_root = root / "ledger"
            approval_root = root / "approvals"
            approvals = JsonApprovalRepository(approval_root)
            harness = Harness(
                model=RunCommandThenFinalLLM(),
                tools=_run_command_registry(root),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    max_steps=3,
                    auto_approve_writes=False,
                    approval_mode="on-risk",
                    approval_root=str(approval_root),
                    operation_ledger_root=str(ledger_root),
                    tool_routing_mode="all",
                    skill_mode="none",
                ),
            )

            waiting = harness.run("validate target.py")
            self.assertEqual(waiting.status.value, "waiting_approval")
            pending = approvals.list_pending()
            self.assertEqual(len(pending), 1)
            self.assertTrue(pending[0].operation_key)
            ledger_files = list(ledger_root.glob("*.json"))
            self.assertEqual(len(ledger_files), 1)
            self.assertEqual(ledger_files[0].stem, pending[0].operation_key)

            approvals.decide(pending[0].operation_key, "approved")
            final = harness.resume(
                waiting.artifact_dir / "task_state" / f"{waiting.run_id}.json"
            )

            self.assertIn("validation finished", final.final_answer or "")
            trace = json.loads(final.trace_path.read_text(encoding="utf-8"))
            self.assertTrue(
                any(
                    event["event_type"] == "tool_execution_started"
                    for event in trace["events"]
                )
            )
            operation = JsonOperationLedgerRepository(ledger_root).get(
                pending[0].operation_key
            )
            self.assertIsNotNone(operation)
            assert operation is not None
            self.assertEqual(operation.status, "executed")

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

            first_runtime = bind_new_runtime_turn(
                first_config,
                first_trace,
                "create generated.py",
            )
            waiting = build_agent_loop(
                first_runtime.config,
                first_trace,
                _create_registry(root),
                CreateThenFinalLLM(),
            ).run(agent_name="CodingAgent")

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
            second_runtime = bind_resume_runtime_turn(
                second_config,
                second_trace,
                first_runtime,
            )
            final = build_agent_loop(
                second_runtime.config,
                second_trace,
                _create_registry(root),
                CreateThenFinalLLM(),
            ).run(agent_name="CodingAgent")

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

    def test_only_one_concurrent_execution_claim_crosses_side_effect_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger_root = root / "ledger"
            target = OperationTarget(
                tool_name="replace_text",
                arguments={"path": "target.py", "old": "a", "new": "b"},
                workspace=tmp,
                action="write",
            )
            key = JsonOperationLedgerRepository.operation_key(target)
            JsonOperationLedgerRepository(ledger_root).ensure_planned(
                OperationPlan(
                    operation_key=key,
                    target=target,
                    status="approved",
                    run_id="approval-run",
                    step=1,
                )
            )

            def claim(run_id: str) -> tuple[str, str]:
                try:
                    JsonOperationLedgerRepository(ledger_root).record_executing(
                        OperationTransition(
                            operation_key=key,
                            status="executing",
                            run_id=run_id,
                            step=2,
                        )
                    )
                except RuntimeError:
                    return "rejected", run_id
                return "executing", run_id

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(claim, ("run-a", "run-b")))

            self.assertEqual([state for state, _ in results].count("executing"), 1)
            self.assertEqual([state for state, _ in results].count("rejected"), 1)
            claimant = next(run_id for state, run_id in results if state == "executing")
            record = JsonOperationLedgerRepository(ledger_root).get(key)
            assert record is not None
            self.assertEqual(record.status, "executing")
            self.assertEqual(record.run_id, claimant)
            self.assertEqual(record.history, ["approved", "executing"])

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
            first_runtime = bind_new_runtime_turn(
                first_config,
                first_trace,
                "fix target",
            )
            build_agent_loop(
                first_runtime.config,
                first_trace,
                _registry(root),
                ReplaceThenFinalLLM(),
            ).run(agent_name="CodingAgent")

            second_trace = TraceRecorder(str(root / "second-trace.json"))
            second_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "second-trace.json"),
                operation_ledger_root=str(ledger_root),
            )
            second_runtime = bind_follow_up_runtime_turn(
                second_config,
                second_trace,
                first_runtime,
            )
            second = build_agent_loop(
                second_runtime.config,
                second_trace,
                _registry(root),
                ReplaceThenFinalLLM(),
            ).run(agent_name="CodingAgent")

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

            first_runtime = bind_new_runtime_turn(
                first_config,
                first_trace,
                "fix target",
            )
            with self.assertRaisesRegex(RuntimeError, "crash before executed commit"):
                build_agent_loop_from_request(
                    AgentLoopBuildRequest(
                        config=first_runtime.config,
                        trace=first_trace,
                        registry=_registry(root),
                        llm=ReplaceThenFinalLLM(),
                        overrides=RuntimeDependencyOverrides(
                            operations=crashing_ledger
                        ),
                    )
                ).run(agent_name="CodingAgent")

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
            second_runtime = bind_resume_runtime_turn(
                second_config,
                second_trace,
                first_runtime,
            )
            second = build_agent_loop(
                second_runtime.config,
                second_trace,
                _registry(root),
                ReplaceThenFinalLLM(),
            ).run(agent_name="CodingAgent")

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
            first_runtime = bind_new_runtime_turn(
                first_config,
                first_trace,
                "fix target",
            )
            build_agent_loop(
                first_runtime.config,
                first_trace,
                _registry(root),
                ReplaceThenFinalLLM(),
            ).run(agent_name="CodingAgent")

            (root / "target.py").write_text("value = 3\n", encoding="utf-8")

            second_trace = TraceRecorder(str(root / "second-trace.json"))
            second_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "second-trace.json"),
                operation_ledger_root=str(ledger_root),
            )
            second_runtime = bind_follow_up_runtime_turn(
                second_config,
                second_trace,
                first_runtime,
            )
            second = build_agent_loop(
                second_runtime.config,
                second_trace,
                _registry(root),
                ReplaceThenFinalLLM(),
            ).run(agent_name="CodingAgent")

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

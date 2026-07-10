import tempfile
import unittest
from pathlib import Path

from agent_forge.observability.trace import TraceRecorder
from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.runtime.operation_ledger import OperationLedgerStore
from agent_forge.runtime.tool_call import ToolCall
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.apply_patch import ApplyPatchTool
from agent_forge.tools.registry import ToolRegistry


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
                        "patch-1",
                        "apply_patch",
                        {"path": "target.py", "old": "value = 1\n", "new": "value = 2\n"},
                    )
                ],
            )
        return AgentResponse("PASS\nfinished", [])


def _registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ApplyPatchTool(WorkspaceSandbox(root), auto_approve_writes=True))
    return registry


class OperationLedgerTest(unittest.TestCase):
    def test_store_records_pending_approved_and_executed_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = OperationLedgerStore(Path(tmp) / "ledger")
            key = OperationLedgerStore.operation_key(
                "apply_patch",
                {"path": "target.py", "old": "a", "new": "b"},
                tmp,
                "apply_patch",
            )

            store.record_pending(key, "apply_patch", {"path": "target.py"}, "apply_patch", tmp, run_id="r1", step=1)
            store.record_approved(key, run_id="r1", step=1)
            store.record_executed(key, run_id="r1", step=2, observation="patched once")

            record = store.get(key)
            self.assertEqual(record.status, "executed")
            self.assertEqual(record.history[-3:], ["pending", "approved", "executed"])

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
            AgentLoop(first_config, first_trace, _registry(root), PatchThenFinalLLM()).run("fix target")

            second_trace = TraceRecorder(str(root / "second-trace.json"))
            second_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "second-trace.json"),
                operation_ledger_root=str(ledger_root),
            )
            second = AgentLoop(second_config, second_trace, _registry(root), PatchThenFinalLLM()).run("fix target")

            self.assertIn("finished", second)
            self.assertEqual((root / "target.py").read_text(encoding="utf-8"), "value = 2\n")
            self.assertTrue(
                any(
                    event["event_type"] == "operation_ledger"
                    and event.get("operation_status") == "skipped_already_executed"
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
            AgentLoop(first_config, first_trace, _registry(root), PatchThenFinalLLM()).run("fix target")

            (root / "target.py").write_text("value = 3\n", encoding="utf-8")

            second_trace = TraceRecorder(str(root / "second-trace.json"))
            second_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "second-trace.json"),
                operation_ledger_root=str(ledger_root),
            )
            second = AgentLoop(second_config, second_trace, _registry(root), PatchThenFinalLLM()).run("fix target")

            self.assertIn("finished", second)
            self.assertEqual((root / "target.py").read_text(encoding="utf-8"), "value = 3\n")
            self.assertTrue(
                any(
                    event["event_type"] == "operation_ledger"
                    and event.get("operation_status") == "stale_operation_record"
                    for event in second_trace.events
                )
            )


if __name__ == "__main__":
    unittest.main()

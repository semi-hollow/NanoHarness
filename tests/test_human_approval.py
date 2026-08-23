import hashlib
import tempfile
import unittest
from pathlib import Path

from agent_forge.observability.api import TraceRecorder
from agent_forge.runtime.adapters import (
    JsonApprovalRepository,
    JsonOperationLedgerRepository,
)
from agent_forge.runtime.api import build_agent_loop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.adapters.openai_compatible import AgentResponse
from agent_forge.runtime.domain.conversation import ToolCall
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.builtins.replace_text import ReplaceTextTool
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
                        {"path": "target.py", "old": "value = 1\n", "new": "value = 2\n"},
                    )
                ],
            )
        return AgentResponse("PASS\npatch applied after approval", [])


def _registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReplaceTextTool(WorkspaceSandbox(root), auto_approve_writes=True))
    return registry


class HumanApprovalTest(unittest.TestCase):
    def test_auto_approved_write_does_not_leave_pending_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            approvals = JsonApprovalRepository(root / "approvals")
            trace = TraceRecorder(str(root / "trace.json"))
            config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "trace.json"),
                auto_approve_writes=True,
                approval_root=str(root / "approvals"),
            )

            final = build_agent_loop(config, trace, _registry(root), ReplaceThenFinalLLM()).run("fix target")

            self.assertIn("patch applied after approval", final)
            self.assertEqual(approvals.list_pending(), [])

    def test_manual_approval_stops_before_write_then_allows_same_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            approvals = JsonApprovalRepository(root / "approvals")

            first_trace = TraceRecorder(str(root / "first-trace.json"))
            first_config = RuntimeConfig(
                workspace=str(root),
                max_steps=2,
                trace_file=str(root / "first-trace.json"),
                auto_approve_writes=False,
                approval_root=str(root / "approvals"),
            )
            first = build_agent_loop(first_config, first_trace, _registry(root), ReplaceThenFinalLLM()).run("fix target")

            self.assertIn("waiting_approval", first)
            self.assertEqual((root / "target.py").read_text(encoding="utf-8"), "value = 1\n")
            pending = approvals.list_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].tool_name, "replace_text")

            approvals.decide(pending[0].operation_key, "approved")

            second_trace = TraceRecorder(str(root / "second-trace.json"))
            second_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "second-trace.json"),
                auto_approve_writes=False,
                approval_root=str(root / "approvals"),
            )
            second = build_agent_loop(second_config, second_trace, _registry(root), ReplaceThenFinalLLM()).run("fix target")

            self.assertIn("patch applied after approval", second)
            self.assertEqual((root / "target.py").read_text(encoding="utf-8"), "value = 2\n")
            self.assertTrue(
                any(
                    event["event_type"] == "human_approval"
                    and event.get("observation") == "approved"
                    for event in second_trace.events
                )
            )

    def test_approved_operation_becomes_stale_when_target_fingerprint_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            approvals = JsonApprovalRepository(root / "approvals")
            ledger_root = root / "ledger"

            first_trace = TraceRecorder(str(root / "first-trace.json"))
            first_config = RuntimeConfig(
                workspace=str(root),
                max_steps=2,
                trace_file=str(root / "first-trace.json"),
                auto_approve_writes=False,
                approval_root=str(root / "approvals"),
                operation_ledger_root=str(ledger_root),
            )
            first = build_agent_loop(first_config, first_trace, _registry(root), ReplaceThenFinalLLM()).run("fix target")

            self.assertIn("waiting_approval", first)
            pending = approvals.list_pending()
            self.assertEqual(len(pending), 1)
            approvals.decide(pending[0].operation_key, "approved")
            original_fingerprint = pending[0].operation_fingerprint
            (root / "target.py").write_text(
                "# independently changed\nvalue = 1\n",
                encoding="utf-8",
            )

            second_trace = TraceRecorder(str(root / "second-trace.json"))
            second_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "second-trace.json"),
                auto_approve_writes=False,
                approval_root=str(root / "approvals"),
                operation_ledger_root=str(ledger_root),
            )
            second = build_agent_loop(second_config, second_trace, _registry(root), ReplaceThenFinalLLM()).run("fix target")

            self.assertIn("approval_stale", second)
            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"),
                "# independently changed\nvalue = 1\n",
            )
            self.assertEqual(approvals.get(pending[0].operation_key).status, "stale")
            self.assertTrue(
                any(
                    event["event_type"] == "human_approval"
                    and event.get("observation") == "approval_stale"
                    for event in second_trace.events
                )
            )

            # 下一次 continuation 不复用 stale，而是为当前目标建立 fresh pending。
            third_trace = TraceRecorder(str(root / "third-trace.json"))
            third_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "third-trace.json"),
                auto_approve_writes=False,
                approval_root=str(root / "approvals"),
                operation_ledger_root=str(ledger_root),
            )
            third = build_agent_loop(
                third_config,
                third_trace,
                _registry(root),
                ReplaceThenFinalLLM(),
            ).run("fix target")

            self.assertIn("waiting_approval", third)
            fresh = approvals.get(pending[0].operation_key)
            self.assertIsNotNone(fresh)
            assert fresh is not None
            self.assertEqual(fresh.status, "pending")
            self.assertNotEqual(fresh.operation_fingerprint, original_fingerprint)
            expected_sha = hashlib.sha256(
                (root / "target.py").read_bytes()
            ).hexdigest()
            self.assertEqual(fresh.operation_fingerprint["sha256"], expected_sha)
            operation = JsonOperationLedgerRepository(ledger_root).get(
                pending[0].operation_key
            )
            self.assertIsNotNone(operation)
            assert operation is not None
            self.assertEqual(operation.pre_fingerprint, fresh.operation_fingerprint)

            # fresh approval 仅授权新 fingerprint；获批后真实工具才执行。
            approvals.decide(fresh.operation_key, "approved")
            fourth_trace = TraceRecorder(str(root / "fourth-trace.json"))
            fourth_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "fourth-trace.json"),
                auto_approve_writes=False,
                approval_root=str(root / "approvals"),
                operation_ledger_root=str(ledger_root),
            )
            fourth = build_agent_loop(
                fourth_config,
                fourth_trace,
                _registry(root),
                ReplaceThenFinalLLM(),
            ).run("fix target")

            self.assertIn("patch applied after approval", fourth)
            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"),
                "# independently changed\nvalue = 2\n",
            )
            self.assertTrue(
                any(
                    event["event_type"] == "tool_execution_started"
                    for event in fourth_trace.events
                )
            )


if __name__ == "__main__":
    unittest.main()

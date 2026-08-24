import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from agent_forge.harness import Harness
from agent_forge.harness_contracts import HarnessConfig
from agent_forge.observability.api import TraceRecorder
from agent_forge.runtime.adapters import (
    JsonApprovalRepository,
    JsonOperationLedgerRepository,
)
from agent_forge.runtime.api import build_agent_loop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.adapters.openai_compatible import AgentResponse
from agent_forge.runtime.domain.conversation import ToolCall
from agent_forge.runtime.domain.approval import ApprovalRequestDraft
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.builtins.replace_text import ReplaceTextTool
from agent_forge.tools.registry import ToolRegistry
from tests.support import bind_new_runtime_turn, bind_resume_runtime_turn


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
    def test_approval_decision_is_write_once_and_concurrent_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            approvals = JsonApprovalRepository(root / "approvals")
            request = approvals.request(
                ApprovalRequestDraft(
                    tool_name="replace_text",
                    arguments={"path": "target.py", "old": "1", "new": "2"},
                    action="write",
                    command="",
                    workspace=str(root),
                    run_id="run-1",
                    step=1,
                    agent_name="CodingAgent",
                    reason="state-changing operation",
                    operation_fingerprint={"sha256": "before"},
                )
            )
            barrier = Barrier(2)

            def decide(status: str) -> str:
                barrier.wait()
                try:
                    return approvals.decide(request.operation_key, status).status
                except ValueError:
                    return "conflict"

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(decide, ["approved", "rejected"]))

            persisted = approvals.get(request.operation_key)
            self.assertIsNotNone(persisted)
            assert persisted is not None
            self.assertIn(persisted.status, {"approved", "rejected"})
            self.assertCountEqual(results, [persisted.status, "conflict"])
            # 相同终态重试幂等；相反决定永远不能改写人工授权事实。
            self.assertEqual(
                approvals.decide(request.operation_key, persisted.status).status,
                persisted.status,
            )
            opposite = "rejected" if persisted.status == "approved" else "approved"
            with self.assertRaisesRegex(ValueError, "immutable"):
                approvals.decide(request.operation_key, opposite)

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

            runtime = bind_new_runtime_turn(config, trace, "fix target")
            final = build_agent_loop(
                runtime.config,
                trace,
                _registry(root),
                ReplaceThenFinalLLM(),
            ).run(agent_name="CodingAgent")

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
            first_runtime = bind_new_runtime_turn(
                first_config,
                first_trace,
                "fix target",
            )
            first = build_agent_loop(
                first_runtime.config,
                first_trace,
                _registry(root),
                ReplaceThenFinalLLM(),
            ).run(agent_name="CodingAgent")

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
            harness = Harness(
                model=ReplaceThenFinalLLM(),
                tools=_registry(root),
                config=HarnessConfig(
                    workspace=str(root),
                    output_root=str(root / "runs"),
                    max_steps=3,
                    auto_approve_writes=False,
                    approval_root=str(root / "approvals"),
                    operation_ledger_root=str(ledger_root),
                    tool_routing_mode="all",
                    skill_mode="none",
                ),
            )

            first = harness.run("fix target")
            self.assertEqual(first.status.value, "waiting_approval")
            pending = approvals.list_pending()
            self.assertEqual(len(pending), 1)
            approvals.decide(pending[0].operation_key, "approved")
            original_fingerprint = pending[0].operation_fingerprint
            (root / "target.py").write_text(
                "# independently changed\nvalue = 1\n",
                encoding="utf-8",
            )

            second = harness.resume(
                first.artifact_dir / "task_state" / f"{first.run_id}.json"
            )
            self.assertEqual(second.status.value, "waiting_approval")
            self.assertEqual(second.stop_reason, "approval_stale")
            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"),
                "# independently changed\nvalue = 1\n",
            )
            self.assertEqual(approvals.get(pending[0].operation_key).status, "stale")
            # Durable trace projection, not an in-memory recorder, owns resume evidence.
            second_events = json.loads(
                second.trace_path.read_text(encoding="utf-8")
            )["events"]
            self.assertTrue(
                any(
                    event["event_type"] == "human_approval"
                    and event.get("observation") == "approval_stale"
                    for event in second_events
                )
            )

            # 同一 pending ToolCall 下次 resume 不复用 stale，而是按当前目标
            # fingerprint 建立 fresh pending，不要求模型重提操作。
            third = harness.resume(
                second.artifact_dir / "task_state" / f"{second.run_id}.json"
            )
            self.assertEqual(third.status.value, "waiting_approval")
            self.assertEqual(third.stop_reason, "waiting_approval")
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
            fourth = harness.resume(
                third.artifact_dir / "task_state" / f"{third.run_id}.json"
            )
            self.assertIn("patch applied after approval", fourth.final_answer or "")
            self.assertEqual(
                (root / "target.py").read_text(encoding="utf-8"),
                "# independently changed\nvalue = 2\n",
            )
            fourth_events = json.loads(
                fourth.trace_path.read_text(encoding="utf-8")
            )["events"]
            self.assertTrue(
                any(
                    event["event_type"] == "tool_execution_started"
                    for event in fourth_events
                )
            )


if __name__ == "__main__":
    unittest.main()

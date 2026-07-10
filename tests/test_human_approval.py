import tempfile
import unittest
from pathlib import Path

from agent_forge.observability.trace import TraceRecorder
from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.approval import ApprovalStore
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import AgentResponse
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
        return AgentResponse("PASS\npatch applied after approval", [])


def _registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ApplyPatchTool(WorkspaceSandbox(root), auto_approve_writes=True))
    return registry


class HumanApprovalTest(unittest.TestCase):
    def test_auto_approved_write_does_not_leave_pending_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            approvals = ApprovalStore(root / "approvals")
            trace = TraceRecorder(str(root / "trace.json"))
            config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "trace.json"),
                auto_approve_writes=True,
                approval_root=str(root / "approvals"),
            )

            final = AgentLoop(config, trace, _registry(root), PatchThenFinalLLM()).run("fix target")

            self.assertIn("patch applied after approval", final)
            self.assertEqual(approvals.list_pending(), [])

    def test_manual_approval_stops_before_write_then_allows_same_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "target.py").write_text("value = 1\n", encoding="utf-8")
            approvals = ApprovalStore(root / "approvals")

            first_trace = TraceRecorder(str(root / "first-trace.json"))
            first_config = RuntimeConfig(
                workspace=str(root),
                max_steps=2,
                trace_file=str(root / "first-trace.json"),
                auto_approve_writes=False,
                approval_root=str(root / "approvals"),
            )
            first = AgentLoop(first_config, first_trace, _registry(root), PatchThenFinalLLM()).run("fix target")

            self.assertIn("waiting_approval", first)
            self.assertEqual((root / "target.py").read_text(encoding="utf-8"), "value = 1\n")
            pending = approvals.list_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].tool_name, "apply_patch")

            approvals.decide(pending[0].operation_key, "approved")

            second_trace = TraceRecorder(str(root / "second-trace.json"))
            second_config = RuntimeConfig(
                workspace=str(root),
                max_steps=3,
                trace_file=str(root / "second-trace.json"),
                auto_approve_writes=False,
                approval_root=str(root / "approvals"),
            )
            second = AgentLoop(second_config, second_trace, _registry(root), PatchThenFinalLLM()).run("fix target")

            self.assertIn("patch applied after approval", second)
            self.assertEqual((root / "target.py").read_text(encoding="utf-8"), "value = 2\n")
            self.assertTrue(
                any(
                    event["event_type"] == "human_approval"
                    and event.get("observation") == "approved"
                    for event in second_trace.events
                )
            )


if __name__ == "__main__":
    unittest.main()

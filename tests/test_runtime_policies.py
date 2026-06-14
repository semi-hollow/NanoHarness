import unittest

from agent_forge.context.memory_policy import MemoryPolicy, MemoryRecord
from agent_forge.observability.evidence import EvidenceLedger
from agent_forge.observability.usage_report import build_usage_report
from agent_forge.runtime.clarification import ClarificationPolicy
from agent_forge.runtime.observation import Observation
from agent_forge.runtime.planning_mode import PlanningModePolicy
from agent_forge.tools.tool_router import ToolRouter


class RuntimePolicyTests(unittest.TestCase):
    def test_clarification_asks_only_for_unresolved_references(self):
        policy = ClarificationPolicy()
        self.assertEqual(policy.decide("fix").action, "proceed")
        decision = policy.decide("按老样子处理一下")
        self.assertEqual(decision.action, "ask")
        self.assertIn("referenced_object", decision.missing_fields)

    def test_tool_router_selects_coding_tools_for_fix_task(self):
        schemas = [{"name": name, "arguments": {}} for name in ["read_file", "apply_patch", "run_command", "ask_human"]]
        route = ToolRouter().route("fix examples/demo_repo/src/calculator.py and run tests", schemas)
        self.assertIn("read_file", route.allowed_names)
        self.assertIn("apply_patch", route.allowed_names)
        self.assertIn("run_command", route.allowed_names)

    def test_memory_policy_filters_private_and_low_confidence_records(self):
        records = [
            MemoryRecord("a", "shared", scope="session", confidence=1.0),
            MemoryRecord("b", "private", scope="agent_private", agent_name="ReviewerAgent", confidence=1.0),
            MemoryRecord("c", "weak", scope="session", confidence=0.2),
        ]
        visible = MemoryPolicy().visible_records(records, agent_name="CodingAgent")
        self.assertEqual([record.key for record in visible], ["a"])

    def test_evidence_refs_enter_usage_report(self):
        ledger = EvidenceLedger()
        item = ledger.add_observation(Observation("run_command", True, "exit_code=0\nOK"))
        trace = {
            "run_id": "r1",
            "events": [
                {"step": 1, "agent_name": "a", "event_type": "evidence_collected", "evidence": item.citation()},
                {"step": 1, "agent_name": "a", "event_type": "final_answer", "evidence_refs": [item.citation()]},
            ],
        }
        usage = build_usage_report(trace)
        self.assertEqual(usage["evidence_refs"], [item.citation()])

    def test_planning_mode_marks_complex_benchmark(self):
        decision = PlanningModePolicy().decide("run WebhookPatchBench end to end")
        self.assertEqual(decision.mode, "plan_execute")


if __name__ == "__main__":
    unittest.main()

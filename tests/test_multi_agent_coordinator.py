import tempfile
import unittest
from pathlib import Path

from agent_forge.multi_agent import MultiAgentCoordinator, get_profile
from agent_forge.observability.trace import TraceRecorder
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.tools.registry import ToolRegistry


class RoleAwareLLM:
    last_usage = None

    def chat(self, messages, tools):
        text = "\n".join(message.content or "" for message in messages)
        if "Reviewer" in text or "SkepticalReviewer" in text:
            return AgentResponse("PASS\nreview looks safe", [])
        if "Verifier" in text or "FactVerifier" in text:
            return AgentResponse("PASS\nvalidation is acceptable for this test", [])
        return AgentResponse("implemented primary role output", [])


class MultiAgentCoordinatorTest(unittest.TestCase):
    def test_coding_fix_profile_runs_roles_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = TraceRecorder(str(root / "trace.json"))
            config = RuntimeConfig(workspace=tmp, max_steps=2, trace_file=str(root / "trace.json"))
            summary = MultiAgentCoordinator(
                "fix a small issue",
                get_profile("coding_fix"),
                config,
                trace,
                ToolRegistry(),
                RoleAwareLLM(),
                run_dir=root,
                max_revision_rounds=1,
            ).run()
            self.assertEqual(summary.status, "passed")
            self.assertEqual([result.role for result in summary.role_results], ["Implementer", "Reviewer", "Verifier"])
            self.assertTrue((root / "multi_agent" / "multi_agent_report.md").exists())
            event_types = {event["event_type"] for event in trace.events}
            self.assertIn("multi_agent_start", event_types)
            self.assertIn("artifact_created", event_types)
            self.assertIn("multi_agent_done", event_types)

    def test_decision_parser_uses_first_line_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = get_profile("research_report")
            coordinator = MultiAgentCoordinator(
                "research",
                profile,
                RuntimeConfig(workspace=tmp, max_steps=1, trace_file=str(root / "trace.json")),
                TraceRecorder(str(root / "trace.json")),
                ToolRegistry(),
                RoleAwareLLM(),
                run_dir=root,
            )
            role = profile.role_by_name("SkepticalReviewer")
            decision = coordinator._decision_for_role(
                role,
                "NEEDS_REVISION\nThis paragraph mentions BLOCKED only as an allowed marker.",
            )
            self.assertEqual(decision, "NEEDS_REVISION")

    def test_role_revision_tools_can_be_artifact_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = get_profile("research_report")
            coordinator = MultiAgentCoordinator(
                "research",
                profile,
                RuntimeConfig(workspace=tmp, max_steps=1, trace_file=str(root / "trace.json")),
                TraceRecorder(str(root / "trace.json")),
                ToolRegistry(),
                RoleAwareLLM(),
                run_dir=root,
            )
            researcher = profile.role_by_name("Researcher")
            self.assertIn("read_file", coordinator._tools_for_role(researcher, 0))
            self.assertEqual(coordinator._tools_for_role(researcher, 1), [])

    def test_primary_raw_tool_markup_triggers_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = get_profile("research_report")
            coordinator = MultiAgentCoordinator(
                "research",
                profile,
                RuntimeConfig(workspace=tmp, max_steps=1, trace_file=str(root / "trace.json")),
                TraceRecorder(str(root / "trace.json")),
                ToolRegistry(),
                RoleAwareLLM(),
                run_dir=root,
            )
            researcher = profile.role_by_name("Researcher")
            decision = coordinator._decision_for_role(
                researcher,
                "<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name=\"read_file\">...</｜｜DSML｜｜tool_calls>",
            )
            self.assertEqual(decision, "NEEDS_REVISION")


if __name__ == "__main__":
    unittest.main()

import tempfile
import subprocess
import unittest
from pathlib import Path

from agent_forge.multi_agent.profiles import get_profile
from agent_forge.multi_agent.wiring import build_multi_agent_coordinator
from agent_forge.observability.api import TraceRecorder
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


class BlockingVerifierLLM:
    last_usage = None

    def chat(self, messages, tools):
        text = "\n".join(message.content or "" for message in messages)
        if "Verifier" in text:
            return AgentResponse("blocked: pending_tool_call_at_stop", [])
        if "Reviewer" in text:
            return AgentResponse("PASS\nreview accepts the candidate patch", [])
        return AgentResponse("implemented primary role output", [])


class BlockingPrimaryLLM:
    last_usage = None

    def chat(self, messages, tools):
        return AgentResponse("blocked: too many consecutive failed tools", [])


def _init_git_with_modified_file(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
    source = root / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "module.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=root, check=True, stdout=subprocess.DEVNULL)
    source.write_text("value = 2\n", encoding="utf-8")


class MultiAgentCoordinatorTest(unittest.TestCase):
    def test_coding_fix_profile_runs_roles_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = TraceRecorder(str(root / "trace.json"))
            config = RuntimeConfig(workspace=tmp, max_steps=2, trace_file=str(root / "trace.json"))
            summary = build_multi_agent_coordinator(
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
            coordinator = build_multi_agent_coordinator(
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

    def test_decision_parser_accepts_markdown_verdict_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = get_profile("coding_fix")
            coordinator = build_multi_agent_coordinator(
                "fix",
                profile,
                RuntimeConfig(workspace=tmp, max_steps=1, trace_file=str(root / "trace.json")),
                TraceRecorder(str(root / "trace.json")),
                ToolRegistry(),
                RoleAwareLLM(),
                run_dir=root,
            )
            reviewer = profile.role_by_name("Reviewer")
            verifier = profile.role_by_name("Verifier")

            self.assertEqual(coordinator._decision_for_role(reviewer, "## Review Verdict: PASS\nLooks safe."), "PASS")
            self.assertEqual(
                coordinator._decision_for_role(verifier, "## Verification Report\n\n**Verdict: PASS**\nEvidence."),
                "PASS",
            )
            self.assertEqual(coordinator._decision_for_role(verifier, "## 验证报告\n\n**裁决：通过**"), "PASS")

    def test_role_revision_tools_can_be_artifact_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = get_profile("research_report")
            coordinator = build_multi_agent_coordinator(
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
            coordinator = build_multi_agent_coordinator(
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

    def test_verifier_block_with_existing_diff_reports_candidate_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_with_modified_file(root)

            trace = TraceRecorder(str(root / "trace.json"))
            config = RuntimeConfig(workspace=tmp, max_steps=2, trace_file=str(root / "trace.json"))
            summary = build_multi_agent_coordinator(
                "fix a small issue",
                get_profile("coding_fix"),
                config,
                trace,
                ToolRegistry(),
                BlockingVerifierLLM(),
                run_dir=root,
                max_revision_rounds=1,
            ).run()

            self.assertEqual(summary.status, "patch_generated")
            self.assertIn("unverified patch", summary.final_answer)

    def test_primary_block_with_existing_diff_reports_candidate_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_with_modified_file(root)

            trace = TraceRecorder(str(root / "trace.json"))
            config = RuntimeConfig(workspace=tmp, max_steps=1, trace_file=str(root / "trace.json"))
            summary = build_multi_agent_coordinator(
                "fix a small issue",
                get_profile("coding_fix"),
                config,
                trace,
                ToolRegistry(),
                BlockingPrimaryLLM(),
                run_dir=root,
                max_revision_rounds=1,
            ).run()

            self.assertEqual(summary.status, "patch_generated")
            self.assertIn("candidate patch generated", summary.final_answer)


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.multi_agent.adapters.artifact_files import FileArtifactRepository
from agent_forge.multi_agent.profiles import get_profile
from agent_forge.multi_agent.domain.models import MultiAgentRunSummary


class MultiAgentArtifactsTest(unittest.TestCase):
    def test_writes_artifacts_index_summary_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FileArtifactRepository(Path(tmp))
            role = get_profile("coding_fix").role_by_name("Implementer")
            artifact = store.write_role_artifact(role, "implemented change", 0)
            self.assertTrue(artifact.path.exists())
            index_path = store.root / "artifact_index.json"
            self.assertTrue(index_path.exists())
            self.assertEqual(json.loads(index_path.read_text(encoding="utf-8"))[0]["id"], artifact.id)

            summary = MultiAgentRunSummary(run_id="r1", task="task", profile="coding_fix", status="passed")
            summary.artifacts = list(store.artifacts)
            summary_path, report_path = store.write_summary(summary)
            self.assertTrue(summary_path.exists())
            self.assertTrue(report_path.exists())
            self.assertIn("Multi-Agent Run Report", report_path.read_text(encoding="utf-8"))

    def test_handoff_context_prioritizes_newest_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FileArtifactRepository(Path(tmp))
            role = get_profile("coding_fix").role_by_name("Implementer")
            first = store.write_role_artifact(role, "first output", 0)
            second = store.write_role_artifact(role, "second output", 1)
            handoff = store.render_handoff_context()
            self.assertLess(handoff.index(second.id), handoff.index(first.id))


if __name__ == "__main__":
    unittest.main()

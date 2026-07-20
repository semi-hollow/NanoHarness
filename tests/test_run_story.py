import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.observability.api import (
    load_run_story,
    read_run_manifest,
    render_run_story,
    write_run_manifest,
)


class RunStoryTest(unittest.TestCase):
    def test_manifest_explains_artifact_lineage_and_claim_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "task_state").mkdir()
            (root / "run_request.json").write_text("{}", encoding="utf-8")
            (root / "trace.json").write_text(
                json.dumps(
                    {
                        "events": [
                            {"event_type": "turn_started"},
                            {"event_type": "tool_call"},
                            {"event_type": "task_state_checkpoint"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "patch.diff").write_text("+candidate\n", encoding="utf-8")
            (root / "task_state" / "run-1.json").write_text("{}", encoding="utf-8")
            (root / "custom.txt").write_text("unowned", encoding="utf-8")

            path = write_run_manifest(
                root,
                run_id="run-1",
                task="fix the regression",
                status="completed",
                stop_reason="final_answer",
            )
            manifest = read_run_manifest(path)

            by_path = {artifact.relative_path: artifact for artifact in manifest.artifacts}
            self.assertEqual(by_path["patch.diff"].evidence_level, "candidate")
            self.assertIn("official resolved", by_path["patch.diff"].does_not_prove)
            self.assertEqual(
                by_path["task_state/run-1.json"].producer_symbol,
                "RunLifecycle.update / stop",
            )
            self.assertEqual(by_path["custom.txt"].kind, "unclassified")

            story = load_run_story(root)
            self.assertEqual(story.evidence_ladder["candidate"], "present")
            self.assertEqual(story.evidence_ladder["official"], "unknown")
            self.assertTrue(
                next(
                    stage
                    for stage in story.stages
                    if stage.stage_id == "tool_governance"
                ).observed
            )
            rendered = render_run_story(story)
            self.assertIn("Artifact Lineage", rendered)
            self.assertIn("does not prove", rendered)

    def test_empty_patch_remains_unknown_candidate_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "patch.diff").write_text("", encoding="utf-8")
            write_run_manifest(
                root,
                run_id="run-empty",
                task="read only",
                status="completed",
                stop_reason="final_answer",
            )

            story = load_run_story(root)
            self.assertEqual(story.evidence_ladder["candidate"], "unknown")


if __name__ == "__main__":
    unittest.main()

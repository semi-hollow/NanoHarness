import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.evaluation.feedback_dataset import export_feedback_dataset, record_feedback


class FeedbackDatasetTest(unittest.TestCase):
    def _write_run(self, root: Path) -> Path:
        run_dir = root / "run-1"
        run_dir.mkdir()
        (run_dir / "trace.json").write_text(
            json.dumps(
                {
                    "run_id": "trace-run-1",
                    "task": "fix the failing parser test",
                    "stop_reason": "final_answer",
                    "final_answer": "candidate patch generated",
                    "events": [
                        {
                            "step": 0,
                            "event_type": "execution_environment",
                            "execution_environment": {
                                "mode": "worktree",
                                "head_sha": "abc123",
                                "dirty": False,
                                "network_policy": "deny",
                                "active_workspace": "/private/workspace",
                            },
                        },
                        {
                            "step": 1,
                            "event_type": "context_assembly",
                            "context": {
                                "selected_files": ["parser.py", "tests/test_parser.py"],
                                "tool_routing": {
                                    "allowed_tools": ["read_file", "apply_patch"],
                                    "dropped_tools": ["run_command"],
                                },
                            },
                        },
                        {
                            "step": 1,
                            "event_type": "action",
                            "tool_call": "apply_patch",
                            "tool_arguments": {"path": "parser.py", "new": "secret source"},
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "patch.diff").write_text("diff --git a/parser.py b/parser.py\n+fixed\n", encoding="utf-8")
        return run_dir

    def test_record_feedback_and_export_safe_evidence_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = self._write_run(root)
            record_feedback(
                run_dir,
                outcome="needs_work",
                labels=["context_miss", "tool_policy"],
                note="Expected validator evidence is missing.",
                reviewer="human",
            )

            output = root / "dataset.jsonl"
            records = export_feedback_dataset([run_dir], output)

            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(record["schema_version"], "agent-forge-eval-v1")
            self.assertEqual(record["task"], "fix the failing parser test")
            self.assertEqual(record["selected_context"], ["parser.py", "tests/test_parser.py"])
            self.assertEqual(record["tool_sequence"], ["apply_patch"])
            self.assertEqual(record["human_feedback"]["outcome"], "needs_work")
            self.assertEqual(record["environment"], {
                "mode": "worktree",
                "head_sha": "abc123",
                "dirty": False,
                "network_policy": "deny",
            })
            self.assertNotIn("patch", record)
            self.assertNotIn("tool_arguments", json.dumps(record))
            self.assertEqual(len(record["patch_sha256"]), 64)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), record)

    def test_export_can_require_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = self._write_run(root)

            records = export_feedback_dataset(
                [run_dir],
                root / "dataset.jsonl",
                require_feedback=True,
            )

            self.assertEqual(records, [])
            self.assertEqual((root / "dataset.jsonl").read_text(encoding="utf-8"), "")

    def test_export_includes_candidate_patch_only_when_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = self._write_run(root)

            records = export_feedback_dataset(
                [run_dir],
                root / "dataset.jsonl",
                include_patch=True,
            )

            self.assertIn("diff --git", records[0]["candidate_patch"])


if __name__ == "__main__":
    unittest.main()

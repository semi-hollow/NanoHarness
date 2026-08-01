import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.evaluation.api import (
    FeedbackRequest,
    ImprovementRecordRequest,
    export_feedback_dataset,
    record_feedback,
    write_improvement_record,
)


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
                                    "allowed_tools": ["read_file", "replace_text"],
                                    "dropped_tools": ["run_command"],
                                },
                            },
                        },
                        {
                            "step": 1,
                            "event_type": "action",
                            "tool_call": "replace_text",
                            "tool_arguments": {
                                "path": "parser.py",
                                "new": "secret source",
                            },
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "candidate_changes.diff").write_text(
            "diff --git a/parser.py b/parser.py\n+fixed\n", encoding="utf-8"
        )
        return run_dir

    def test_record_feedback_and_export_safe_evidence_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = self._write_run(root)
            record_feedback(
                FeedbackRequest(
                    target=run_dir,
                    outcome="needs_work",
                    labels=("context_miss", "tool_policy"),
                    note="Expected validator evidence is missing.",
                    reviewer="human",
                )
            )

            output = root / "dataset.jsonl"
            records = export_feedback_dataset([run_dir], output)

            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(record["schema_version"], "agent-forge-eval-v1")
            self.assertEqual(record["task"], "fix the failing parser test")
            self.assertEqual(
                record["selected_context"], ["parser.py", "tests/test_parser.py"]
            )
            self.assertEqual(record["tool_sequence"], ["replace_text"])
            self.assertEqual(record["human_feedback"]["outcome"], "needs_work")
            self.assertEqual(
                record["environment"],
                {
                    "mode": "worktree",
                    "head_sha": "abc123",
                    "dirty": False,
                    "network_policy": "deny",
                },
            )
            self.assertNotIn("patch", record)
            self.assertNotIn("tool_arguments", json.dumps(record))
            self.assertEqual(len(record["candidate_diff_sha256"]), 64)
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

    def test_export_includes_candidate_diff_only_when_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = self._write_run(root)

            records = export_feedback_dataset(
                [run_dir],
                root / "dataset.jsonl",
                include_patch=True,
            )

            self.assertIn("diff --git", records[0]["candidate_diff"])

    def test_improvement_record_connects_existing_campaign_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            campaign = Path(tmp)
            (campaign / "summary.json").write_text(
                json.dumps(
                    {
                        "campaign_id": "campaign-1",
                        "config_digest": "digest",
                        "source": {"revision": "abc123"},
                        "variants": {
                            "minimal-control": {
                                "official_evaluated": 2,
                                "official_resolved": 2,
                                "tool_calls": 27,
                                "failed_tool_calls": 8,
                                "total_tokens": 100,
                                "estimated_cost_usd": 0.1,
                            },
                            "governed-runtime": {
                                "official_evaluated": 2,
                                "official_resolved": 2,
                                "tool_calls": 32,
                                "failed_tool_calls": 5,
                                "total_tokens": 130,
                                "estimated_cost_usd": 0.13,
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            (campaign / "manifest.json").write_text(
                json.dumps(
                    {
                        "config": {
                            "case_ids": ["case-a", "case-b"],
                            "comparison_factor": "runtime-preset",
                        }
                    }
                ),
                encoding="utf-8",
            )

            path = write_improvement_record(
                ImprovementRecordRequest(
                    campaign_dir=campaign,
                    observed_problem="Baseline tool failures are noisy.",
                    hypothesis="Governed routing lowers failed tool calls.",
                    change_ref="governed-runtime preset",
                    decision="iterate",
                    decision_rationale="Correctness tied and cost increased.",
                    claim_boundary="Two commissioning cases only.",
                    diagnosis_finding="Validation environment failures dominated.",
                    diagnosis_evidence=("Control failed 8/27 calls.",),
                )
            )

            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["regression_cases"], ["case-a", "case-b"])
            self.assertEqual(record["before_after"]["delta"]["failed_tool_calls"], -3)
            self.assertEqual(record["before_after"]["delta"]["official_resolved"], 0)
            self.assertEqual(record["diagnosis"]["review_status"], "reviewed")
            self.assertEqual(record["before_after"]["control"]["tool_calls"], 27)
            self.assertIn("Validation environment", record["diagnosis"]["finding"])
            self.assertEqual(
                record["diagnosis"]["evidence"], ["Control failed 8/27 calls."]
            )
            self.assertEqual(record["decision"]["status"], "iterate")
            self.assertIn("commissioning", record["claim_boundary"].lower())


if __name__ == "__main__":
    unittest.main()

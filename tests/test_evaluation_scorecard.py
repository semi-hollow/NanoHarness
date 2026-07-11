import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.evaluation.scorecard import build_benchmark_scorecard, write_benchmark_scorecard


class EvaluationScorecardTest(unittest.TestCase):
    def _write_usage(self, root: Path, instance_id: str, *, tokens=100, cost=0.1, latency=1000, failed=0):
        case_dir = root / "cases" / instance_id
        case_dir.mkdir(parents=True)
        usage_report = case_dir / "usage_report.md"
        usage_report.write_text("usage", encoding="utf-8")
        (case_dir / "usage.json").write_text(
            json.dumps(
                {
                    "summary": {
                        "llm_calls": 2,
                        "total_tokens": tokens,
                        "estimated_cost_usd": cost,
                        "llm_latency_ms": latency,
                        "tool_calls": 4,
                        "failed_tool_calls": failed,
                    },
                    "steps": [{"llm_calls": [{"model": "deepseek-chat"}]}],
                }
            ),
            encoding="utf-8",
        )
        return usage_report

    def test_scorecard_uses_separate_local_and_official_denominators(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            usage_1 = self._write_usage(root, "case-1")
            usage_2 = self._write_usage(root, "case-2")
            usage_3 = self._write_usage(root, "case-3", failed=2)
            results = {
                "run_id": "run-1",
                "dataset_name": "SWE-bench_Lite",
                "split": "test",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "tool_routing_mode": "task-aware",
                "max_steps": 16,
                "max_context_chars": 12000,
                "case_results": [
                    {
                        "instance_id": "case-1",
                        "status": "patch_generated",
                        "patch_chars": 10,
                        "usage_report_path": str(usage_1),
                        "local_validation_status": "passed",
                        "official_evaluation_status": "not_evaluated",
                        "failure_class": "locally_verified_candidate",
                    },
                    {
                        "instance_id": "case-2",
                        "status": "patch_generated",
                        "patch_chars": 20,
                        "usage_report_path": str(usage_2),
                        "local_validation_status": "failed",
                        "official_evaluation_status": "official_resolved",
                        "failure_class": "official_resolved",
                    },
                    {
                        "instance_id": "case-3",
                        "status": "no_patch",
                        "patch_chars": 0,
                        "usage_report_path": str(usage_3),
                        "local_validation_status": "not_run",
                        "official_evaluation_status": "official_eval_error",
                        "failure_class": "official_eval_error",
                    },
                ],
            }

            scorecard = build_benchmark_scorecard(results, root)

        metrics = scorecard["metrics"]
        self.assertEqual(metrics["case_count"], 3)
        self.assertEqual(metrics["patch_generated_count"], 2)
        self.assertAlmostEqual(metrics["patch_generated_rate"], 2 / 3)
        self.assertEqual(metrics["local_verified_count"], 1)
        self.assertEqual(metrics["official_evaluated_count"], 1)
        self.assertEqual(metrics["official_resolved_count"], 1)
        self.assertEqual(metrics["official_resolved_rate"], 1.0)
        self.assertEqual(metrics["total_tokens"], 300)
        self.assertAlmostEqual(metrics["estimated_cost_usd"], 0.3)
        self.assertEqual(metrics["failed_tool_calls"], 2)
        self.assertEqual(scorecard["metadata"]["observed_models"], ["deepseek-chat"])
        self.assertEqual(scorecard["metadata"]["max_steps"], 16)
        self.assertEqual(scorecard["metadata"]["max_context_chars"], 12000)

    def test_scorecard_does_not_turn_missing_official_eval_into_zero_percent(self):
        results = {
            "run_id": "run-2",
            "dataset_name": "dataset",
            "split": "test",
            "provider": "provider",
            "model": "model",
            "case_results": [
                {
                    "instance_id": "case-1",
                    "status": "patch_generated",
                    "patch_chars": 10,
                    "local_validation_status": "not_run",
                    "official_evaluation_status": "not_evaluated",
                }
            ],
        }
        scorecard = build_benchmark_scorecard(results, Path("."))
        self.assertIsNone(scorecard["metrics"]["official_resolved_rate"])

    def test_scorecard_records_observed_container_image_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir = root / "cases" / "case-1"
            case_dir.mkdir(parents=True)
            (case_dir / "execution_environment.json").write_text(
                json.dumps(
                    {
                        "probe": {
                            "mode": "container",
                            "container_image": "python:3.11-slim",
                            "container_image_id": "sha256:immutable-image",
                        }
                    }
                ),
                encoding="utf-8",
            )
            results = {
                "run_id": "run-container",
                "dataset_name": "dataset",
                "split": "test",
                "provider": "provider",
                "model": "model",
                "execution_mode": "container",
                "container_image": "python:3.11-slim",
                "case_results": [{"instance_id": "case-1", "patch_chars": 0}],
            }

            scorecard = build_benchmark_scorecard(results, root)

        self.assertEqual(
            scorecard["metadata"]["observed_container_image_ids"],
            ["sha256:immutable-image"],
        )
        self.assertEqual(scorecard["cases"][0]["execution_mode"], "container")

    def test_writer_creates_machine_and_reviewer_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = {
                "run_id": "run-3",
                "dataset_name": "dataset",
                "split": "test",
                "provider": "provider",
                "model": "model",
                "case_results": [],
            }
            json_path, report_path = write_benchmark_scorecard(results, root)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            report = report_path.read_text(encoding="utf-8")
        self.assertEqual(payload["schema_version"], 1)
        self.assertIn("Evidence Denominators", report)
        self.assertIn("No official resolved rate", report)

    def test_variant_aggregation_derives_official_denominator_from_status(self):
        results = {
            "run_id": "run-4",
            "dataset_name": "dataset",
            "split": "test",
            "provider": "provider",
            "model": "model",
            "case_results": [],
            "variant_comparisons": {
                "case-1": {
                    "variants": {
                        "agent_runtime": {
                            "patch_generated": True,
                            "official_evaluation_status": "official_resolved",
                            "official_resolved": True,
                        }
                    }
                }
            },
        }
        scorecard = build_benchmark_scorecard(results, Path("."))
        metrics = scorecard["variants"]["agent_runtime"]
        self.assertEqual(metrics["official_evaluated_count"], 1)
        self.assertEqual(metrics["official_resolved_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()

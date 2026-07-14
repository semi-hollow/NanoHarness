import tempfile
import unittest
from pathlib import Path

from agent_forge.bench.domain.models import BenchCaseResult, BenchRunSummary
from agent_forge.bench.presentation.report import render_bench_report, write_bench_artifacts


class BenchReportTests(unittest.TestCase):
    def test_write_artifacts_includes_quantitative_scorecard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = BenchRunSummary(
                run_id="run-scorecard",
                dataset_name="local",
                split="test",
                provider="deepseek",
                model="default",
                output_dir=root,
                predictions_path=root / "predictions.jsonl",
            )

            write_bench_artifacts(summary)

            self.assertTrue((root / "scorecard.json").exists())
            self.assertTrue((root / "scorecard.md").exists())

    def test_report_separates_candidate_patch_from_official_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / "trace.json"
            usage = root / "usage_report.md"
            patch = root / "patch.diff"
            trace.write_text("{}", encoding="utf-8")
            usage.write_text("usage", encoding="utf-8")
            patch.write_text("diff", encoding="utf-8")
            case = BenchCaseResult(
                instance_id="case-1",
                repo="local/repo",
                workspace=root,
                trace_path=trace,
                usage_report_path=usage,
                patch_path=patch,
                status="patch_generated",
                final_answer="candidate patch generated",
                evaluation_status="not_evaluated",
                patch_chars=4,
                failure_class="patch_generated_but_unverified",
                diagnosis="candidate patch only",
                diagnosis_evidence=["patch_chars=4", "eval=not_evaluated"],
                next_actions=["run official evaluation"],
            )
            summary = BenchRunSummary(
                run_id="run-1",
                dataset_name="local",
                split="test",
                provider="deepseek",
                model="default",
                output_dir=root,
                predictions_path=root / "predictions.jsonl",
                case_results=[case],
            )
            report = render_bench_report(summary)
        self.assertIn("Evidence Levels", report)
        self.assertIn("candidate patch", report.lower())
        self.assertIn("official", report.lower())
        self.assertIn("patch_generated_but_unverified", report)
        self.assertIn("run official evaluation", report)

    def test_report_shows_baseline_comparison_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / "trace.json"
            usage = root / "usage_report.md"
            patch = root / "patch.diff"
            trace.write_text("{}", encoding="utf-8")
            usage.write_text("usage", encoding="utf-8")
            patch.write_text("diff", encoding="utf-8")
            case = BenchCaseResult(
                instance_id="case-1",
                repo="local/repo",
                workspace=root,
                trace_path=trace,
                usage_report_path=usage,
                patch_path=patch,
                status="patch_generated",
                final_answer="candidate patch generated",
                patch_chars=4,
            )
            summary = BenchRunSummary(
                run_id="run-1",
                dataset_name="local",
                split="test",
                provider="deepseek",
                model="default",
                output_dir=root,
                predictions_path=root / "predictions.jsonl",
                case_results=[case],
                variant_comparisons={
                    "case-1": {
                        "recommendation": "agent_runtime produced a candidate patch where direct_baseline did not.",
                        "variants": {
                            "direct_baseline": {"patch_generated": False, "failure_class": "context_miss"},
                            "agent_runtime": {"patch_generated": True, "failure_class": "patch_generated_but_unverified"},
                        },
                    }
                },
            )
            report = render_bench_report(summary)
        self.assertIn("## Baseline Comparison", report)
        self.assertIn("direct_baseline", report)
        self.assertIn("agent_runtime", report)
        self.assertNotIn("governed_agent", report)
        self.assertIn("agent_runtime produced", report)

    def test_report_separates_local_validation_from_parsed_official_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / "trace.json"
            patch = root / "patch.diff"
            trace.write_text("{}", encoding="utf-8")
            patch.write_text("diff", encoding="utf-8")
            case = BenchCaseResult(
                instance_id="case-1",
                repo="local/repo",
                workspace=root,
                trace_path=trace,
                usage_report_path=None,
                patch_path=patch,
                status="patch_generated",
                final_answer="candidate",
                patch_chars=4,
                local_validation_status="passed",
                official_evaluation_status="official_eval_failed",
                evaluation_status="official_eval_failed",
            )
            summary = BenchRunSummary(
                run_id="run-1",
                dataset_name="local",
                split="test",
                provider="deepseek",
                model="default",
                output_dir=root,
                predictions_path=root / "predictions.jsonl",
                case_results=[case],
            )
            report = render_bench_report(summary)
        self.assertIn("local validation", report.lower())
        self.assertIn("official evaluation", report.lower())
        self.assertIn("official_eval_failed", report)
        self.assertNotIn("has not parsed per-case", report)
        self.assertIn("scorecard.md", report)


if __name__ == "__main__":
    unittest.main()

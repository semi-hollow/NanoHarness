import tempfile
import unittest

from agent_forge.evaluation import compare_runs, extract_run_metrics, write_evaluation_artifacts
from agent_forge.evaluation.comparison import compare_variants


class EvaluationComparisonTest(unittest.TestCase):
    def test_compare_runs_writes_json_and_report(self):
        comparison = compare_runs(
            "case-1",
            {"status": "blocked", "patch_chars": 0, "llm_calls": 2, "tool_calls": 1},
            {
                "status": "passed",
                "patch_chars": 120,
                "llm_calls": 4,
                "tool_calls": 3,
                "revision_rounds": 1,
                "reviewer_findings": ["reviewer caught missing validation"],
                "verifier_status": "PASS",
            },
        )
        self.assertTrue(comparison.multi_patch_generated)
        self.assertIn("multi-agent may be worth", comparison.recommendation)
        with tempfile.TemporaryDirectory() as tmp:
            json_path, report_path = write_evaluation_artifacts(comparison, tmp)
            self.assertTrue(json_path.exists())
            self.assertTrue(report_path.exists())
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("## Executive Summary", report)
            self.assertIn("## Side-by-Side Metrics", report)
            self.assertIn("## Multi-Agent Review Loop", report)
            self.assertIn("## Failure Taxonomy", report)
            self.assertIn("## Failure Lens", report)
            self.assertIn("Model / Provider", report)
            self.assertIn("Tool / Runtime", report)
            self.assertIn("| Metric | Single | Multi | Delta |", report)
            self.assertIn("official SWE-bench", report)

    def test_extract_run_metrics_reads_usage_and_multi_agent_summary(self):
        metrics = extract_run_metrics(
            {
                "status": "patch_generated",
                "patch_chars": 42,
                "evaluation_status": "not_evaluated",
                "failure_class": "",
            },
            {
                "summary": {
                    "estimated_cost_usd": 0.125,
                    "llm_calls": 3,
                    "tool_calls": 7,
                    "failed_tool_calls": 2,
                }
            },
            {
                "status": "needs_revision",
                "revision_rounds": 2,
                "role_results": [
                    {"role": "Reviewer", "decision": "NEEDS_REVISION", "final_answer": "NEEDS_REVISION\nmissing focused test"},
                    {"role": "Verifier", "decision": "PASS", "final_answer": "PASS\nvalidation ok"},
                ],
            },
        )
        self.assertEqual(metrics["status"], "patch_generated")
        self.assertTrue(metrics["patch_generated"])
        self.assertEqual(metrics["official_eval_status"], "not_evaluated")
        self.assertEqual(metrics["estimated_cost_usd"], 0.125)
        self.assertEqual(metrics["llm_calls"], 3)
        self.assertEqual(metrics["tool_calls"], 7)
        self.assertEqual(metrics["failed_tool_calls"], 2)
        self.assertEqual(metrics["revision_rounds"], 2)
        self.assertEqual(metrics["verifier_status"], "PASS")
        self.assertEqual(metrics["reviewer_findings"], ["missing focused test"])

    def test_extract_run_metrics_uses_safe_defaults_for_missing_artifacts(self):
        metrics = extract_run_metrics({"status": "blocked", "patch_chars": 0, "failure_class": "provider_config"})
        self.assertEqual(metrics["status"], "blocked")
        self.assertFalse(metrics["patch_generated"])
        self.assertEqual(metrics["official_eval_status"], "unavailable")
        self.assertEqual(metrics["estimated_cost_usd"], 0.0)
        self.assertEqual(metrics["llm_calls"], 0)
        self.assertEqual(metrics["tool_calls"], 0)
        self.assertEqual(metrics["failed_tool_calls"], 0)
        self.assertEqual(metrics["failure_taxonomy"], "provider_config")

    def test_compare_variants_explains_agent_loop_value(self):
        result = compare_variants(
            "case-1",
            {
                "direct_baseline": {"patch_generated": False, "estimated_cost_usd": 0.01, "failure_class": "context_miss"},
                "single_agent": {"patch_generated": True, "tool_calls": 8, "estimated_cost_usd": 0.04, "failure_class": "patch_generated_but_unverified"},
                "governed_agent": {"patch_generated": True, "tool_calls": 6, "failed_tool_calls": 0, "estimated_cost_usd": 0.045, "failure_class": "patch_generated_but_unverified"},
            },
        )
        self.assertEqual(result["task_id"], "case-1")
        self.assertFalse(result["variants"]["direct_baseline"]["patch_generated"])
        self.assertTrue(result["variants"]["single_agent"]["patch_generated"])
        self.assertIn("AgentLoop", result["before_after_summary"])
        self.assertIn("governed", result["recommendation"].lower())

    def test_compare_variants_stays_conservative_when_cost_only_increases(self):
        result = compare_variants(
            "case-2",
            {
                "direct_baseline": {"patch_generated": True, "estimated_cost_usd": 0.01},
                "single_agent": {"patch_generated": True, "estimated_cost_usd": 0.05, "failed_tool_calls": 2},
                "governed_agent": {"patch_generated": True, "estimated_cost_usd": 0.07, "failed_tool_calls": 3},
            },
        )
        self.assertIn("global claim", result["recommendation"].lower())


if __name__ == "__main__":
    unittest.main()

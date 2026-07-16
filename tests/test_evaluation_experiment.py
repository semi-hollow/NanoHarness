import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.evaluation.api import compare_benchmark_scorecards, write_ablation_comparison


def _scorecard(
    run_id,
    cases,
    *,
    model="deepseek-chat",
    routing="task-aware",
    skill_mode="auto",
    skill_names=None,
    memory_recall_limit=0,
    memory_snapshot_sha256="snapshot-a",
    max_prompt_tokens=32768,
):
    official_evaluated = sum(case["official_evaluated"] for case in cases)
    official_resolved = sum(case["official_resolved"] for case in cases)
    return {
        "schema_version": 1,
        "metadata": {
            "run_id": run_id,
            "dataset_name": "SWE-bench_Lite",
            "split": "test",
            "provider": "deepseek",
            "requested_model": model,
            "observed_models": [model],
            "tool_routing_mode": routing,
            "skill_mode": skill_mode,
            "skill_names": list(skill_names or []),
            "skill_manifest_sha256": "builtins_only",
            "max_prompt_tokens": max_prompt_tokens,
            "reserved_output_tokens": 4096,
            "max_tool_calls_per_turn": 4,
            "cost_budget_usd": None,
            "timeout_seconds": 900.0,
            "memory_namespace": "swebench:<instance_id>",
            "memory_recall_limit": memory_recall_limit,
            "memory_snapshot_sha256": memory_snapshot_sha256,
            "execution_mode": "local",
            "network_policy": "deny",
            "agent_mode": "single",
            "max_steps": 16,
            "max_context_chars": 12000,
            "max_revision_rounds": 0,
        },
        "metrics": {
            "case_count": len(cases),
            "patch_generated_count": sum(case["patch_generated"] for case in cases),
            "local_verified_count": sum(case["local_verified"] for case in cases),
            "official_evaluated_count": official_evaluated,
            "official_resolved_count": official_resolved,
            "official_resolved_rate": official_resolved / official_evaluated if official_evaluated else None,
            "total_tokens": sum(case["total_tokens"] for case in cases),
            "estimated_cost_usd": sum(case["estimated_cost_usd"] for case in cases),
            "llm_latency_ms": sum(case["llm_latency_ms"] for case in cases),
            "tool_calls": sum(case["tool_calls"] for case in cases),
            "failed_tool_calls": sum(case["failed_tool_calls"] for case in cases),
        },
        "cases": cases,
    }


def _case(instance_id, *, patch=False, local=False, official=None, tokens=100, cost=0.1, failed=0):
    return {
        "instance_id": instance_id,
        "patch_generated": patch,
        "local_verified": local,
        "official_evaluated": official is not None,
        "official_resolved": official is True,
        "official_evaluation_status": (
            "official_resolved" if official is True else "official_eval_failed" if official is False else "not_evaluated"
        ),
        "failure_class": "official_resolved" if official else "patch_generated_but_unverified" if patch else "no_patch_generated",
        "total_tokens": tokens,
        "estimated_cost_usd": cost,
        "llm_latency_ms": 1000,
        "tool_calls": 4,
        "failed_tool_calls": failed,
    }


class EvaluationExperimentTest(unittest.TestCase):
    def test_paired_ablation_reports_quality_and_efficiency_deltas(self):
        control = _scorecard(
            "control",
            [_case("case-1", patch=False, official=False, failed=2), _case("case-2", patch=True, official=False)],
            routing="all",
        )
        treatment = _scorecard(
            "treatment",
            [_case("case-1", patch=True, local=True, official=True), _case("case-2", patch=True, official=False)],
            routing="task-aware",
        )

        comparison = compare_benchmark_scorecards(control, treatment, factor="tool-routing")

        self.assertTrue(comparison["validity"]["comparable"])
        self.assertEqual(comparison["aggregate_delta"]["official_resolved_count"], 1)
        self.assertEqual(comparison["aggregate_delta"]["failed_tool_calls"], -2)
        paired = {row["instance_id"]: row for row in comparison["paired_cases"]}
        self.assertEqual(paired["case-1"]["outcome"], "official_improved")
        self.assertIn("official resolved", comparison["conclusion"].lower())

    def test_ablation_rejects_different_models(self):
        control = _scorecard("control", [_case("case-1")], model="model-a")
        treatment = _scorecard("treatment", [_case("case-1")], model="model-b")
        with self.assertRaisesRegex(ValueError, "model identity"):
            compare_benchmark_scorecards(control, treatment, factor="prompt")

    def test_skill_ablation_allows_only_skill_configuration_to_change(self):
        control = _scorecard(
            "control",
            [_case("case-1", official=False)],
            skill_mode="none",
        )
        treatment = _scorecard(
            "treatment",
            [_case("case-1", official=True)],
            skill_mode="auto",
            skill_names=["targeted_code_edit"],
        )

        comparison = compare_benchmark_scorecards(
            control,
            treatment,
            factor="skills",
        )

        self.assertTrue(comparison["validity"]["comparable"])
        self.assertEqual(
            comparison["aggregate_delta"]["official_resolved_count"],
            1,
        )

    def test_ablation_rejects_undeclared_runtime_config_drift(self):
        control = _scorecard("control", [_case("case-1")], routing="all")
        treatment = _scorecard("treatment", [_case("case-1")], routing="task-aware")
        treatment["metadata"]["max_steps"] = 8
        with self.assertRaisesRegex(ValueError, "max_steps"):
            compare_benchmark_scorecards(control, treatment, factor="tool-routing")

    def test_memory_ablation_requires_same_frozen_snapshot(self):
        control = _scorecard(
            "control",
            [_case("case-1")],
            memory_recall_limit=0,
        )
        treatment = _scorecard(
            "treatment",
            [_case("case-1")],
            memory_recall_limit=6,
        )

        comparison = compare_benchmark_scorecards(
            control,
            treatment,
            factor="memory",
        )
        self.assertTrue(comparison["validity"]["comparable"])

        treatment["metadata"]["memory_snapshot_sha256"] = "snapshot-b"
        with self.assertRaisesRegex(ValueError, "memory_snapshot_sha256"):
            compare_benchmark_scorecards(
                control,
                treatment,
                factor="memory",
            )

    def test_context_ablation_allows_only_window_budget_to_change(self):
        control = _scorecard(
            "control",
            [_case("case-1")],
            max_prompt_tokens=8192,
        )
        treatment = _scorecard(
            "treatment",
            [_case("case-1")],
            max_prompt_tokens=32768,
        )

        comparison = compare_benchmark_scorecards(
            control,
            treatment,
            factor="context-window",
        )

        self.assertTrue(comparison["validity"]["comparable"])

    def test_ablation_rejects_undeclared_execution_environment_drift(self):
        control = _scorecard("control", [_case("case-1")])
        treatment = _scorecard("treatment", [_case("case-1")])
        treatment["metadata"]["execution_mode"] = "container"
        with self.assertRaisesRegex(ValueError, "execution_mode"):
            compare_benchmark_scorecards(control, treatment, factor="prompt")

    def test_ablation_rejects_container_image_id_drift(self):
        control = _scorecard("control", [_case("case-1")])
        treatment = _scorecard("treatment", [_case("case-1")])
        control["metadata"]["observed_container_image_ids"] = ["sha256:one"]
        treatment["metadata"]["observed_container_image_ids"] = ["sha256:two"]
        with self.assertRaisesRegex(ValueError, "observed_container_image_ids"):
            compare_benchmark_scorecards(control, treatment, factor="tool-routing")

    def test_ablation_does_not_claim_quality_from_patch_rate_only(self):
        control = _scorecard("control", [_case("case-1", patch=False)], routing="all")
        treatment = _scorecard("treatment", [_case("case-1", patch=True)], routing="task-aware")
        comparison = compare_benchmark_scorecards(control, treatment, factor="tool-routing")
        self.assertIn("correctness effect is unknown", comparison["conclusion"].lower())

    def test_ablation_does_not_call_new_official_evidence_an_improvement(self):
        control = _scorecard("control", [_case("case-1", patch=True, official=None)], routing="all")
        treatment = _scorecard("treatment", [_case("case-1", patch=True, official=True)], routing="task-aware")
        comparison = compare_benchmark_scorecards(control, treatment, factor="tool-routing")
        self.assertEqual(comparison["paired_cases"][0]["outcome"], "official_evidence_added")
        self.assertNotIn("improved official resolved", comparison["conclusion"].lower())
        self.assertIn("not comparable", comparison["conclusion"].lower())

    def test_writer_creates_ablation_json_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control_dir = root / "control"
            treatment_dir = root / "treatment"
            output_dir = root / "comparison"
            control_dir.mkdir()
            treatment_dir.mkdir()
            (control_dir / "scorecard.json").write_text(
                json.dumps(_scorecard("control", [_case("case-1")], routing="all")), encoding="utf-8"
            )
            (treatment_dir / "scorecard.json").write_text(
                json.dumps(_scorecard("treatment", [_case("case-1")], routing="task-aware")), encoding="utf-8"
            )

            json_path, report_path = write_ablation_comparison(
                control_dir,
                treatment_dir,
                factor="tool-routing",
                output_dir=output_dir,
            )

            report = report_path.read_text(encoding="utf-8")
            self.assertTrue(json_path.exists())
            self.assertIn("Paired Ablation", report)
            self.assertIn("single run per variant", report)


if __name__ == "__main__":
    unittest.main()

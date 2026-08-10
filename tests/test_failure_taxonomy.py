import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.bench.adapters.case_evidence import JsonCaseEvidenceReader
from agent_forge.bench.application.failure_analysis import BenchFailureAnalyzer
from agent_forge.bench.domain.failure_taxonomy import FailureDiagnosis
from agent_forge.bench.domain.models import BenchCaseResult

analyze_case_result = BenchFailureAnalyzer(
    JsonCaseEvidenceReader()
).classify_case_failure


class FailureTaxonomyTest(unittest.TestCase):
    def test_failure_diagnosis_requires_named_fields(self):
        with self.assertRaises(TypeError):
            FailureDiagnosis("failure", "summary", [], [])  # type: ignore[misc]

    def _result(
        self,
        root: Path,
        *,
        status="blocked",
        final_answer="",
        error="",
        patch_chars=0,
        evaluation_status="not_evaluated",
        stop_reason="",
        failed_tool_calls=0,
    ):
        trace_path = root / "trace.json"
        trace_path.write_text(
            json.dumps({"stop_reason": stop_reason or status}),
            encoding="utf-8",
        )
        usage_json = root / "usage.json"
        usage_json.write_text(
            json.dumps(
                {
                    "summary": {
                        "llm_calls": 3,
                        "tool_calls": 5,
                        "failed_tool_calls": failed_tool_calls,
                        "total_tokens": 1000,
                    },
                    "steps": [{"context": {"selected_files_count": 0}}],
                }
            ),
            encoding="utf-8",
        )
        usage_report = root / "usage_report.md"
        usage_report.write_text("usage", encoding="utf-8")
        candidate_diff_path = root / "candidate_changes.diff"
        candidate_diff_path.write_text("x" * patch_chars, encoding="utf-8")
        return BenchCaseResult(
            instance_id="case-1",
            repo="local/repo",
            workspace=root,
            trace_path=trace_path,
            usage_report_path=usage_report,
            candidate_diff_path=candidate_diff_path,
            status=status,
            final_answer=final_answer,
            patch_chars=patch_chars,
            error=error,
            evaluation_status=evaluation_status,
        )

    def test_patch_generated_is_not_called_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            diagnosis = analyze_case_result(
                self._result(Path(tmp), status="patch_generated", patch_chars=12)
            )
        self.assertEqual(diagnosis.failure_class, "patch_generated_but_unverified")
        self.assertEqual(diagnosis.rule_id, "patch_generated_but_unverified")
        self.assertEqual(diagnosis.source, "ordered_rule_taxonomy")
        self.assertEqual(diagnosis.taxonomy_version, "1.2")
        self.assertIn("official", " ".join(diagnosis.next_actions).lower())
        self.assertIn("candidate", diagnosis.summary.lower())

    def test_validation_environment_unavailable_beats_tool_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(
                Path(tmp),
                final_answer=(
                    "python_validation: missing dependency erfa; validation_blocked"
                ),
            )
            diagnosis = analyze_case_result(result)
        self.assertEqual(diagnosis.failure_class, "validation_environment_unavailable")
        self.assertIn("environment", diagnosis.impact.lower())

    def test_tool_schema_mismatch_has_engineering_lesson(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(
                Path(tmp),
                final_answer="read_file ignored offset limit; wrong line window",
            )
            diagnosis = analyze_case_result(result)
        self.assertEqual(diagnosis.failure_class, "tool_schema_mismatch")
        self.assertIn("schema", diagnosis.engineering_lesson.lower())

    def test_context_window_overflow_beats_repository_context_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(
                Path(tmp),
                final_answer=(
                    "blocked: invalid llm response: "
                    "context_length_exceeded after compaction"
                ),
            )
            diagnosis = analyze_case_result(result)

        self.assertEqual(diagnosis.failure_class, "context_window_exceeded")
        self.assertIn("complete model request", diagnosis.summary.lower())

    def test_input_policy_block_is_not_reported_as_repository_context_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(
                Path(tmp),
                final_answer="blocked: blocked risky input: https://",
            )
            diagnosis = analyze_case_result(result)

        self.assertEqual(diagnosis.failure_class, "input_policy_block")
        self.assertIn("before the first model call", diagnosis.summary.lower())

    def test_official_eval_error_is_not_reported_as_patch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(
                Path(tmp), evaluation_status="official_eval_error", patch_chars=12
            )
            diagnosis = analyze_case_result(result)
        self.assertEqual(diagnosis.failure_class, "official_eval_error")
        self.assertIn("harness", diagnosis.summary.lower())
        self.assertNotIn("rejected", diagnosis.summary.lower())

    def test_official_rejection_beats_local_missing_dependency_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(
                Path(tmp),
                final_answer="python_validation: no module named asgiref",
                patch_chars=12,
            )
            result.official_evaluation_status = "official_eval_failed"
            diagnosis = analyze_case_result(result)

        self.assertEqual(diagnosis.failure_class, "official_eval_failed")

    def test_provider_marker_in_runner_error_is_not_collapsed_into_generic_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(
                Path(tmp),
                error="request_timeout while reading provider response",
            )
            diagnosis = analyze_case_result(result)

        self.assertEqual(diagnosis.failure_class, "provider_transport_error")

    def test_official_resolved_is_not_labeled_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(
                Path(tmp), evaluation_status="official_resolved", patch_chars=12
            )
            result.official_evaluation_status = "official_resolved"
            diagnosis = analyze_case_result(result)
        self.assertEqual(diagnosis.failure_class, "official_resolved")
        self.assertNotIn("unverified", diagnosis.summary.lower())

    def test_local_test_pass_is_not_labeled_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(
                Path(tmp), evaluation_status="local_verified", patch_chars=12
            )
            result.local_validation_status = "passed"
            diagnosis = analyze_case_result(result)
        self.assertEqual(diagnosis.failure_class, "locally_verified_candidate")
        self.assertIn("official", diagnosis.summary.lower())

    def test_local_pass_without_patch_is_not_a_verified_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(Path(tmp), status="no_patch", patch_chars=0)
            result.local_validation_status = "passed"
            diagnosis = analyze_case_result(result)

        self.assertEqual(
            diagnosis.failure_class,
            "local_validation_passed_without_patch",
        )
        self.assertIn("unchanged", diagnosis.summary.lower())

    def test_cost_budget_exhaustion_beats_generic_tool_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(
                Path(tmp),
                final_answer="blocked: cost budget exceeded",
                stop_reason="cost_budget_exceeded",
                failed_tool_calls=6,
            )
            diagnosis = analyze_case_result(result)

        self.assertEqual(diagnosis.failure_class, "runtime_budget_exhausted")
        self.assertIn("unfinished", diagnosis.impact.lower())

    def test_tool_failure_circuit_breaker_beats_generic_tool_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(
                Path(tmp),
                final_answer=(
                    "blocked: too many consecutive failed tools: 3 >= limit 3"
                ),
                stop_reason="too many consecutive failed tools: 3 >= limit 3",
                failed_tool_calls=3,
            )
            diagnosis = analyze_case_result(result)

        self.assertEqual(
            diagnosis.failure_class,
            "tool_failure_circuit_breaker",
        )
        self.assertIn("recovery", diagnosis.engineering_lesson.lower())


if __name__ == "__main__":
    unittest.main()

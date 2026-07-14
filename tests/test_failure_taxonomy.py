import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.bench.adapters.case_evidence import JsonCaseEvidenceReader
from agent_forge.bench.application.diagnostics import DiagnoseBenchCase
from agent_forge.bench.domain.models import BenchCaseResult

diagnose_case_result = DiagnoseBenchCase(JsonCaseEvidenceReader()).diagnose


class FailureTaxonomyTest(unittest.TestCase):
    def _result(
        self,
        root: Path,
        *,
        status="blocked",
        final_answer="",
        error="",
        patch_chars=0,
        evaluation_status="not_evaluated",
    ):
        trace_path = root / "trace.json"
        trace_path.write_text(json.dumps({"stop_reason": status}), encoding="utf-8")
        usage_json = root / "usage.json"
        usage_json.write_text(
            json.dumps(
                {
                    "summary": {
                        "llm_calls": 3,
                        "tool_calls": 5,
                        "failed_tool_calls": 0,
                        "total_tokens": 1000,
                    },
                    "steps": [{"context": {"selected_files_count": 0}}],
                }
            ),
            encoding="utf-8",
        )
        usage_report = root / "usage_report.md"
        usage_report.write_text("usage", encoding="utf-8")
        patch_path = root / "patch.diff"
        patch_path.write_text("x" * patch_chars, encoding="utf-8")
        return BenchCaseResult(
            instance_id="case-1",
            repo="local/repo",
            workspace=root,
            trace_path=trace_path,
            usage_report_path=usage_report,
            patch_path=patch_path,
            status=status,
            final_answer=final_answer,
            patch_chars=patch_chars,
            error=error,
            evaluation_status=evaluation_status,
        )

    def test_patch_generated_is_not_called_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            diagnosis = diagnose_case_result(self._result(Path(tmp), status="patch_generated", patch_chars=12))
        self.assertEqual(diagnosis.failure_class, "patch_generated_but_unverified")
        self.assertIn("official", " ".join(diagnosis.next_actions).lower())
        self.assertIn("candidate", diagnosis.summary.lower())

    def test_validation_environment_unavailable_beats_tool_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(Path(tmp), final_answer="diagnostics: missing dependency erfa; validation_blocked")
            diagnosis = diagnose_case_result(result)
        self.assertEqual(diagnosis.failure_class, "validation_environment_unavailable")
        self.assertIn("environment", diagnosis.impact.lower())

    def test_tool_schema_mismatch_has_engineering_lesson(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(Path(tmp), final_answer="read_file ignored offset limit; wrong line window")
            diagnosis = diagnose_case_result(result)
        self.assertEqual(diagnosis.failure_class, "tool_schema_mismatch")
        self.assertIn("schema", diagnosis.engineering_lesson.lower())

    def test_official_eval_error_is_not_reported_as_patch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(Path(tmp), evaluation_status="official_eval_error", patch_chars=12)
            diagnosis = diagnose_case_result(result)
        self.assertEqual(diagnosis.failure_class, "official_eval_error")
        self.assertIn("harness", diagnosis.summary.lower())
        self.assertNotIn("rejected", diagnosis.summary.lower())

    def test_official_resolved_is_not_labeled_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(Path(tmp), evaluation_status="official_resolved", patch_chars=12)
            result.official_evaluation_status = "official_resolved"
            diagnosis = diagnose_case_result(result)
        self.assertEqual(diagnosis.failure_class, "official_resolved")
        self.assertNotIn("unverified", diagnosis.summary.lower())

    def test_local_test_pass_is_not_labeled_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(Path(tmp), evaluation_status="local_verified", patch_chars=12)
            result.local_validation_status = "passed"
            diagnosis = diagnose_case_result(result)
        self.assertEqual(diagnosis.failure_class, "locally_verified_candidate")
        self.assertIn("official", diagnosis.summary.lower())


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.bench.diagnostics import diagnose_case_result
from agent_forge.bench.types import BenchCaseResult


class FailureTaxonomyTest(unittest.TestCase):
    def _result(self, root: Path, *, status="blocked", final_answer="", error="", patch_chars=0):
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

    def test_tool_schema_mismatch_has_interview_lesson(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._result(Path(tmp), final_answer="read_file ignored offset limit; wrong line window")
            diagnosis = diagnose_case_result(result)
        self.assertEqual(diagnosis.failure_class, "tool_schema_mismatch")
        self.assertIn("schema", diagnosis.interview_lesson.lower())


if __name__ == "__main__":
    unittest.main()

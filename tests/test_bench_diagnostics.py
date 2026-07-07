import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.bench.diagnostics import diagnose_case_result
from agent_forge.bench.types import BenchCaseResult


class BenchDiagnosticsTest(unittest.TestCase):
    def _result(self, root: Path, final_answer: str) -> BenchCaseResult:
        trace_path = root / "trace.json"
        trace_path.write_text(json.dumps({"stop_reason": "blocked"}), encoding="utf-8")
        usage_path = root / "usage.json"
        usage_path.write_text(
            json.dumps(
                {
                    "summary": {
                        "llm_calls": 4,
                        "tool_calls": 7,
                        "failed_tool_calls": 0,
                        "total_tokens": 19585,
                    },
                    "steps": [{"context": {"selected_files_count": 0}}],
                }
            ),
            encoding="utf-8",
        )
        usage_report_path = root / "usage_report.md"
        usage_report_path.write_text("usage", encoding="utf-8")
        patch_path = root / "patch.diff"
        patch_path.write_text("", encoding="utf-8")
        return BenchCaseResult(
            instance_id="case-1",
            repo="local/repo",
            workspace=root,
            trace_path=trace_path,
            usage_report_path=usage_report_path,
            patch_path=patch_path,
            status="blocked",
            final_answer=final_answer,
            patch_chars=0,
        )

    def test_pending_tool_call_stop_is_not_misclassified_as_context_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            diagnosis = diagnose_case_result(self._result(Path(tmp), "blocked: pending_tool_call_at_stop"))
        self.assertEqual(diagnosis.failure_class, "pending_tool_call_at_stop")

    def test_provider_incomplete_read_is_classified_before_context_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            diagnosis = diagnose_case_result(
                self._result(Path(tmp), "blocked: role Implementer failed with exception: IncompleteRead(790 bytes read)")
            )
        self.assertEqual(diagnosis.failure_class, "provider_transport_error")


if __name__ == "__main__":
    unittest.main()

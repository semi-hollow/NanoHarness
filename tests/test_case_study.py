import tempfile
import unittest
from pathlib import Path

from agent_forge.bench.domain.models import BenchCaseResult
from agent_forge.bench.presentation.case_study import render_case_study, write_case_study


class CaseStudyTests(unittest.TestCase):
    def test_case_study_renders_runtime_lesson_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / "trace.json"
            usage = root / "usage_report.md"
            patch = root / "patch.diff"
            trace.write_text('{"events": []}', encoding="utf-8")
            usage.write_text("usage", encoding="utf-8")
            patch.write_text("diff --git a/x b/x", encoding="utf-8")
            result = BenchCaseResult(
                instance_id="astropy__astropy-12907",
                repo="astropy/astropy",
                workspace=root,
                trace_path=trace,
                usage_report_path=usage,
                patch_path=patch,
                status="patch_generated",
                final_answer="",
                evaluation_status="not_evaluated",
                patch_chars=20,
                failure_class="patch_generated_but_unverified",
                diagnosis="candidate patch only",
                diagnosis_evidence=["patch_chars=20", "eval=not_evaluated"],
                next_actions=["run official evaluation"],
            )
            text = render_case_study(result)
            path = write_case_study(result)
            self.assertEqual(path.name, "case_study.md")
            self.assertTrue(path.exists())
        self.assertIn("# Case Study: astropy__astropy-12907", text)
        self.assertIn("Runtime Lesson", text)
        self.assertIn("patch_generated_but_unverified", text)

    def test_write_case_study_overwrites_updated_evaluation_and_diagnosis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / "trace.json"
            usage = root / "usage_report.md"
            patch = root / "patch.diff"
            trace.write_text('{"events": []}', encoding="utf-8")
            usage.write_text("usage", encoding="utf-8")
            patch.write_text("diff --git a/x b/x", encoding="utf-8")
            result = BenchCaseResult(
                instance_id="local__case-1",
                repo="local/repo",
                workspace=root,
                trace_path=trace,
                usage_report_path=usage,
                patch_path=patch,
                status="patch_generated",
                final_answer="",
                evaluation_status="not_evaluated",
                patch_chars=20,
                failure_class="patch_generated_but_unverified",
                diagnosis="candidate patch only",
            )

            path = write_case_study(result)
            self.assertIn("evaluation_status: `not_evaluated`", path.read_text(encoding="utf-8"))

            result.evaluation_status = "official_eval_failed"
            result.official_evaluation_status = "official_eval_failed"
            result.failure_class = "official_eval_failed"
            result.diagnosis = "official evaluation failed"
            write_case_study(result)

            artifact = path.read_text(encoding="utf-8")
            self.assertIn("evaluation_status: `official_eval_failed`", artifact)
            self.assertIn("official_evaluation_status: `official_eval_failed`", artifact)
            self.assertIn("- class: `official_eval_failed`", artifact)
            self.assertIn("- diagnosis: official evaluation failed", artifact)
            self.assertNotIn("- evaluation_status: `not_evaluated`", artifact)
            self.assertNotIn("- official_evaluation_status: `not_evaluated`", artifact)


if __name__ == "__main__":
    unittest.main()

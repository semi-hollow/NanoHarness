import tempfile
import unittest
from pathlib import Path

from agent_forge.bench.case_study import render_case_study, write_case_study
from agent_forge.bench.types import BenchCaseResult


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


if __name__ == "__main__":
    unittest.main()

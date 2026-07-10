import unittest
import tempfile
from pathlib import Path

from agent_forge.evaluation.mini_cases import evaluate_mini_case, load_mini_cases, write_mini_case_report


class MiniCasesTest(unittest.TestCase):
    def test_non_coding_agent_cases_are_loadable_and_use_common_eval_dimensions(self):
        cases = {case.case_id: case for case in load_mini_cases()}

        self.assertIn("research-citation-quality", cases)
        self.assertIn("ops-approval-workflow", cases)
        self.assertNotEqual(cases["research-citation-quality"].domain, "coding")
        self.assertIn("evidence_quality", cases["research-citation-quality"].eval_dimensions)
        self.assertIn("human_intervention_count", cases["ops-approval-workflow"].eval_dimensions)

    def test_evaluate_mini_case_scores_evidence_and_writes_report(self):
        case = {case.case_id: case for case in load_mini_cases()}["research-citation-quality"]
        result = evaluate_mini_case(
            case,
            {
                "artifacts": ["briefing.md", "source_limitations.md"],
                "citations": ["source-a", "source-b"],
                "unsupported_claim_count": 0,
                "tool_calls": 4,
                "safety_violation": False,
            },
        )

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.dimension_scores["task_success"]["status"], "passed")
        self.assertEqual(result.dimension_scores["evidence_quality"]["status"], "passed")
        self.assertEqual(result.dimension_scores["unsupported_claim_count"]["value"], 0)

        with tempfile.TemporaryDirectory() as tmp:
            report_path = write_mini_case_report(case, result, Path(tmp))

            report = report_path.read_text(encoding="utf-8")
            self.assertIn("# Mini Case Evaluation", report)
            self.assertIn("research-citation-quality", report)
            self.assertIn("not a benchmark leaderboard", report)


if __name__ == "__main__":
    unittest.main()

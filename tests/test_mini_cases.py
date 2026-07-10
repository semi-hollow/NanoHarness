import unittest

from agent_forge.evaluation.mini_cases import load_mini_cases


class MiniCasesTest(unittest.TestCase):
    def test_non_coding_agent_cases_are_loadable_and_use_common_eval_dimensions(self):
        cases = {case.case_id: case for case in load_mini_cases()}

        self.assertIn("research-citation-quality", cases)
        self.assertIn("ops-approval-workflow", cases)
        self.assertNotEqual(cases["research-citation-quality"].domain, "coding")
        self.assertIn("evidence_quality", cases["research-citation-quality"].eval_dimensions)
        self.assertIn("human_intervention_count", cases["ops-approval-workflow"].eval_dimensions)


if __name__ == "__main__":
    unittest.main()

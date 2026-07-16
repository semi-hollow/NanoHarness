import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.bench.api import (
    get_regression_set_profile,
    inspect_swebench_case,
    list_regression_case_profiles,
)
from agent_forge.bench.presentation.case_inspection import (
    render_case_catalog,
    render_case_inspection,
)


class BenchmarkCaseInspectionTest(unittest.TestCase):
    def test_catalog_explains_universe_selection_and_claim_boundary(self):
        set_profile = get_regression_set_profile("smoke-5")
        profiles = list_regression_case_profiles("smoke-5")

        document = render_case_catalog(set_profile, profiles)

        self.assertEqual(set_profile.universe_case_count, 300)
        self.assertEqual(len(profiles), 5)
        self.assertIn("候选全集：`300`", document)
        self.assertIn("人工分层选择", document)
        self.assertIn("不能代表 SWE-bench Lite 总体表现", document)
        for profile in profiles:
            self.assertIn(profile.instance_id, document)
            self.assertIn(profile.selection_reason, document)

    def test_case_inspection_hides_evaluation_material_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases_file = Path(tmp) / "cases.json"
            cases_file.write_text(
                json.dumps([_sample_case()]),
                encoding="utf-8",
            )

            inspection = inspect_swebench_case(
                "local__case-1",
                cases_file=str(cases_file),
            )
            document = render_case_inspection(inspection)
            payload = inspection.to_dict()

        self.assertIn("Fix the public behavior", document)
        self.assertIn("tests/test_feature.py::test_target", document)
        self.assertIn("Official test patch：隐藏", document)
        self.assertIn("Gold patch：隐藏", document)
        self.assertNotIn("SECRET_TEST_ASSERTION", document)
        self.assertNotIn("SECRET_GOLD_ANSWER", document)
        self.assertNotIn("test_patch", payload)
        self.assertNotIn("gold_patch", payload)

    def test_case_inspection_reveals_material_only_with_explicit_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases_file = Path(tmp) / "cases.jsonl"
            cases_file.write_text(json.dumps(_sample_case()) + "\n", encoding="utf-8")
            inspection = inspect_swebench_case(
                "local__case-1",
                cases_file=str(cases_file),
            )

        document = render_case_inspection(
            inspection,
            show_test_patch=True,
            show_gold_patch=True,
        )
        payload = inspection.to_dict(
            include_test_patch=True,
            include_gold_patch=True,
        )
        self.assertIn("SECRET_TEST_ASSERTION", document)
        self.assertIn("SECRET_GOLD_ANSWER", document)
        self.assertEqual(payload["gold_patch_summary"]["hunks"], 1)
        self.assertEqual(payload["gold_patch_summary"]["files"], ["feature.py"])


def _sample_case() -> dict:
    return {
        "instance_id": "local__case-1",
        "repo": "local/project",
        "base_commit": "abc123",
        "version": "1.0",
        "problem_statement": "Fix the public behavior without changing compatibility.",
        "hints_text": "Read feature.py.",
        "FAIL_TO_PASS": json.dumps(["tests/test_feature.py::test_target"]),
        "PASS_TO_PASS": ["tests/test_feature.py::test_existing"],
        "test_patch": "diff --git a/tests/test_feature.py b/tests/test_feature.py\n+SECRET_TEST_ASSERTION\n",
        "patch": (
            "diff --git a/feature.py b/feature.py\n"
            "--- a/feature.py\n"
            "+++ b/feature.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+SECRET_GOLD_ANSWER\n"
        ),
    }


if __name__ == "__main__":
    unittest.main()

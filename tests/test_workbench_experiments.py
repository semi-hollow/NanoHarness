"""实验对比 Workbench 的目录、渲染和证据边界。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.workbench.adapters.experiment_files import FileExperimentCatalog
from agent_forge.workbench.presentation.experiments import render_experiment_bundle
from agent_forge.workbench.presentation.http import INDEX_HTML


PROJECT_DIR = Path(__file__).resolve().parents[1]


class WorkbenchExperimentCatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = FileExperimentCatalog(PROJECT_DIR)

    def test_active_experiments_form_two_level_catalog_with_case_drilldown(
        self,
    ) -> None:
        sources = self.catalog.experiment_sources()
        families = {source.family_key for source in sources}
        comparisons = {source.comparison_key for source in sources}

        self.assertEqual(families, {"tool-aci", "absolute-capability"})
        self.assertEqual(comparisons, {"r0-vs-r1", "r0-vs-r2", "mini50-v1"})
        for experiment_id, expected_cases in (
            ("tool-aci-r1", 20),
            ("tool-aci-r2", 20),
            ("mini50-v1-deepseek-v4-flash", 50),
        ):
            experiment_sources = [
                source for source in sources if source.experiment_id == experiment_id
            ]
            self.assertEqual(
                [source.item_key for source in experiment_sources[:4]],
                ["overview", "variables", "results", "evidence"],
            )
            self.assertEqual(
                sum(source.item_kind == "case" for source in experiment_sources),
                expected_cases,
            )

    def test_r2_pages_show_exact_variable_result_and_case_transitions(self) -> None:
        variables = self.catalog.experiment_bundle("tool-aci-r2:variables")
        results = self.catalog.experiment_bundle("tool-aci-r2:results")
        gain = self.catalog.experiment_bundle("tool-aci-r2:case:sympy__sympy-20590")
        regression = self.catalog.experiment_bundle(
            "tool-aci-r2:case:astropy__astropy-14182"
        )
        self.assertIsNotNone(variables)
        self.assertIsNotNone(results)
        self.assertIsNotNone(gain)
        self.assertIsNotNone(regression)

        variables_html = render_experiment_bundle(variables)  # type: ignore[arg-type]
        self.assertIn("rg --fixed-strings", variables_html)
        self.assertIn("find_files(pattern, path, max_results)", variables_html)
        self.assertIn("render_output_window", variables_html)
        self.assertIn("563a99fe72b078fa91bfb682d60d6d19f398a864", variables_html)
        self.assertIn("92f4de56a1391b58e8e249471ebd4ec04102f60b", variables_html)

        results_html = render_experiment_bundle(results)  # type: ignore[arg-type]
        self.assertIn("14/20", results_html)
        self.assertIn("-33.6%", results_html)
        self.assertIn("-20.8%", results_html)
        self.assertIn("Gain：Unresolved → Resolved", results_html)
        self.assertIn("Regression：Resolved → Unresolved", results_html)

        gain_html = render_experiment_bundle(gain)  # type: ignore[arg-type]
        self.assertIn("sympy__sympy-20590", gain_html)
        self.assertIn("Unresolved → Resolved", gain_html)
        self.assertIn("33", gain_html)
        self.assertIn("22", gain_html)

        regression_html = render_experiment_bundle(regression)  # type: ignore[arg-type]
        self.assertIn("astropy__astropy-14182", regression_html)
        self.assertIn("Resolved → Unresolved", regression_html)

    def test_mini50_result_uses_complete_denominator_and_case_classification(
        self,
    ) -> None:
        results = self.catalog.experiment_bundle("mini50-v1-deepseek-v4-flash:results")
        empty_case = self.catalog.experiment_bundle(
            "mini50-v1-deepseek-v4-flash:case:django__django-11206"
        )
        self.assertIsNotNone(results)
        self.assertIsNotNone(empty_case)

        results_html = render_experiment_bundle(results)  # type: ignore[arg-type]
        self.assertIn("28/50", results_html)
        self.assertIn("16", results_html)
        self.assertIn("Agent Empty Patch", results_html)
        self.assertIn("provider=0", results_html)
        self.assertIn("terminal=50", results_html)

        case_html = render_experiment_bundle(empty_case)  # type: ignore[arg-type]
        self.assertIn("django__django-11206", case_html)
        self.assertIn("Agent terminal Empty Patch", case_html)
        self.assertIn("运行证据 → Mini-50 → 当前 Case", case_html)

    def test_evidence_page_separates_raw_derived_and_reviewed_layers(self) -> None:
        bundle = self.catalog.experiment_bundle("tool-aci-r1:evidence")
        self.assertIsNotNone(bundle)
        rendered = render_experiment_bundle(bundle)  # type: ignore[arg-type]

        self.assertIn("原始测量", rendered)
        self.assertIn("确定性派生", rendered)
        self.assertIn("审阅解释", rendered)
        self.assertIn("private_raw_run_artifacts", rendered)
        self.assertIn("machine_comparison_result", rendered)
        self.assertIn("reviewed_interpretation", rendered)

    def test_catalog_rejects_manifest_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            experiment_dir = root / "benchmarks" / "experiments" / "escape"
            experiment_dir.mkdir(parents=True)
            outside = Path(temp_dir) / "outside.json"
            outside.write_text('{"status":"completed"}', encoding="utf-8")
            (experiment_dir / "experiment.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "artifact_type": "nanoharness_experiment_view",
                        "active": True,
                        "experiment_id": "escape",
                        "experiment_kind": "measurement",
                        "family": {"id": "escape", "title": "Escape"},
                        "comparison": {"id": "escape", "title": "Escape"},
                        "paths": {
                            "plan": "../outside.json",
                            "result": "../outside.json",
                        },
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(FileExperimentCatalog(root).experiment_sources(), ())

    def test_index_exposes_one_workbench_with_runtime_and_experiment_modes(
        self,
    ) -> None:
        self.assertIn('data-mode="runtime"', INDEX_HTML)
        self.assertIn('data-mode="experiments"', INDEX_HTML)
        self.assertIn('id="experimentFamilySelect"', INDEX_HTML)
        self.assertIn('id="experimentComparisonSelect"', INDEX_HTML)
        self.assertIn('id="experimentItemSelect"', INDEX_HTML)
        self.assertIn("/api/experiment", INDEX_HTML)


if __name__ == "__main__":
    unittest.main()

"""实验对比 Workbench 的目录、渲染和证据边界。"""

from __future__ import annotations

import json
import hashlib
import shutil
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

        self.assertEqual(
            families,
            {"tool-aci", "absolute-capability", "engineering-history"},
        )
        self.assertEqual(
            comparisons,
            {
                "r0-vs-r1",
                "r0-vs-r2",
                "mini50-v1",
                "runtime-preset-50x2",
                "context-budget-exploration",
                "runtime-quality-golden-10",
                "operation-ledger-replay",
            },
        )
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

    def test_tool_aci_paired_pages_keep_r0_r1_and_r0_r2_unambiguous(self) -> None:
        r1 = self.catalog.experiment_bundle("tool-aci-r1:results")
        r2 = self.catalog.experiment_bundle("tool-aci-r2:results")
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)

        r1_html = render_experiment_bundle(r1)  # type: ignore[arg-type]
        self.assertIn("R0→R1", r1_html)
        self.assertIn("14/20", r1_html)
        self.assertIn("13/20", r1_html)
        self.assertIn("1 / 2", r1_html)
        self.assertIn("Treatment design", r1_html)
        self.assertIn("reject", r1_html)

        r2_html = render_experiment_bundle(r2)  # type: ignore[arg-type]
        self.assertIn("R0→R2", r2_html)
        self.assertIn("14/20", r2_html)
        self.assertIn("-33.6%", r2_html)
        self.assertIn("-20.8%", r2_html)
        self.assertIn("1 / 1", r2_html)
        self.assertIn("Treatment design", r2_html)
        self.assertIn("reject", r2_html)

    def test_engineering_history_is_archived_hashed_and_read_only(self) -> None:
        history_path = (
            PROJECT_DIR / "benchmarks/showcase/engineering-history-v1.json"
        )
        history = json.loads(history_path.read_text(encoding="utf-8"))
        self.assertEqual(history["schema_version"], 1)
        self.assertEqual(len(history["entries"]), 4)
        self.assertEqual(
            [entry["id"] for entry in history["entries"]],
            [
                "runtime-preset-50x2",
                "context-budget-exploration",
                "runtime-quality-golden-10",
                "operation-ledger-replay",
            ],
        )
        for entry in history["entries"]:
            source_path = PROJECT_DIR / entry["source_path"]
            self.assertTrue(source_path.is_file())
            self.assertEqual(
                hashlib.sha256(source_path.read_bytes()).hexdigest(),
                entry["source_sha256"],
            )
        serialized = json.dumps(history, ensure_ascii=False)
        for forbidden in ("/Users/", "api_key", "provider_secret", "raw_prompt"):
            self.assertNotIn(forbidden, serialized)

        golden = self.catalog.experiment_bundle(
            "engineering-history:runtime-quality-golden-10:overview"
        )
        ledger = self.catalog.experiment_bundle(
            "engineering-history:operation-ledger-replay:overview"
        )
        self.assertIsNotNone(golden)
        self.assertIsNotNone(ledger)
        golden_html = render_experiment_bundle(golden)  # type: ignore[arg-type]
        ledger_html = render_experiment_bundle(ledger)  # type: ignore[arg-type]
        self.assertIn(
            "Runtime Quality Golden-10 · R0 / R1 / R2 / R3 Historical Evolution",
            golden_html,
        )
        self.assertIn("ENGINEERING HISTORY · ARCHIVED", golden_html)
        self.assertIn("不同 planned 分母不可按百分比横向排名", golden_html)
        self.assertIn("0/2 → 2/2 resolved", ledger_html)
        self.assertIn("5/10 → 4/10 resolved", ledger_html)

        with tempfile.TemporaryDirectory() as temporary:
            fresh_root = Path(temporary)
            showcase = fresh_root / "benchmarks/showcase"
            showcase.mkdir(parents=True)
            shutil.copy2(history_path, showcase / history_path.name)
            archive_source = PROJECT_DIR / "benchmarks/archive/legacy-benchmarks"
            archive_target = fresh_root / "benchmarks/archive/legacy-benchmarks"
            shutil.copytree(archive_source, archive_target)

            fresh_catalog = FileExperimentCatalog(fresh_root)
            fresh_history = [
                source
                for source in fresh_catalog.experiment_sources()
                if source.family_key == "engineering-history"
            ]
            self.assertEqual(len(fresh_history), 4)

            mutated = (
                archive_target
                / "experiments/03-runtime-quality-golden-10/README.md"
            )
            mutated.write_text("mutated", encoding="utf-8")
            remaining = [
                source.comparison_key
                for source in fresh_catalog.experiment_sources()
                if source.family_key == "engineering-history"
            ]
            self.assertNotIn("runtime-quality-golden-10", remaining)

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
        self.assertIn("Empty Patch Failure Review", results_html)
        self.assertIn("provider_model_request_latency_overran_run_budget", results_html)
        self.assertIn("provider=0", results_html)
        self.assertIn("terminal=50", results_html)

        case_html = render_experiment_bundle(empty_case)  # type: ignore[arg-type]
        self.assertIn("django__django-11206", case_html)
        self.assertIn("Agent terminal Empty Patch", case_html)
        self.assertIn("command_capability_recovery_dead_end", case_html)
        self.assertIn("First write", case_html)
        self.assertIn("Next design lever", case_html)
        self.assertIn("运行证据 → Mini-50 → 当前 Case", case_html)

    def test_empty_patch_review_is_sanitized_complete_and_fresh_clone_readable(
        self,
    ) -> None:
        review_path = (
            PROJECT_DIR
            / "benchmarks/experiments/mini50-v1-deepseek-v4-flash"
            / "empty-patch-failure-review-v1.json"
        )
        review = json.loads(review_path.read_text(encoding="utf-8"))
        result = json.loads(
            (
                PROJECT_DIR
                / "benchmarks/experiments/mini50-v1-deepseek-v4-flash/result.json"
            ).read_text(encoding="utf-8")
        )
        cases = review["cases"]
        self.assertEqual(review["schema_version"], 1)
        self.assertEqual(review["case_count"], 6)
        self.assertEqual(len(cases), 6)
        self.assertEqual(
            {item["case_id"] for item in cases},
            set(result["case_ids"]["agent_terminal_empty_patch"]),
        )
        for item in cases:
            self.assertEqual(len(item["provenance"]["trace_sha256"]), 64)
            self.assertTrue(item["provenance"]["run_id"])
            self.assertGreater(item["provenance"]["trace_event_count"], 0)
            self.assertIsNone(item["first_successful_write"])
            self.assertFalse(item["repeated_identical_call_detected"])
        serialized = json.dumps(review, ensure_ascii=False)
        for forbidden in ("/Users/", "api_key", "provider_secret", "raw_prompt"):
            self.assertNotIn(forbidden, serialized)

        with tempfile.TemporaryDirectory() as temporary:
            fresh_root = Path(temporary)
            experiment_target = (
                fresh_root
                / "benchmarks/experiments/mini50-v1-deepseek-v4-flash"
            )
            experiment_target.parent.mkdir(parents=True)
            shutil.copytree(review_path.parent, experiment_target)
            shutil.copy2(
                PROJECT_DIR / "benchmarks/experiments/artifact-provenance.json",
                fresh_root / "benchmarks/experiments/artifact-provenance.json",
            )
            showcase = fresh_root / "benchmarks/showcase"
            showcase.mkdir(parents=True)
            shutil.copy2(
                PROJECT_DIR / "benchmarks/showcase/swebench-verified-mini-50-v1.json",
                showcase / "swebench-verified-mini-50-v1.json",
            )
            catalog = FileExperimentCatalog(fresh_root)
            fresh_results = catalog.experiment_bundle(
                "mini50-v1-deepseek-v4-flash:results"
            )
            fresh_case = catalog.experiment_bundle(
                "mini50-v1-deepseek-v4-flash:case:pydata__xarray-3151"
            )
            self.assertIsNotNone(fresh_results)
            self.assertIsNotNone(fresh_case)
            self.assertIn(
                "Empty Patch Failure Review",
                render_experiment_bundle(fresh_results),  # type: ignore[arg-type]
            )
            self.assertIn(
                "10041778 ms",
                render_experiment_bundle(fresh_case),  # type: ignore[arg-type]
            )

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

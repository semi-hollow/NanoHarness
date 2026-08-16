import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from examples.debug_lab import run as debug_lab
from examples.debug_lab import support as debug_support
from scripts.install_pycharm_debug_lab import (
    LAB_GROUP,
    READING_SCOPES,
    TARGETS,
    install_breakpoints,
    install_reading_scopes,
    resolve_breakpoints,
)


PROJECT_ROOT = Path(__file__).parents[1]


class DebugLabSupportTest(unittest.TestCase):
    def test_coordinated_lab_runs_parallel_repairs_then_edge_case_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            state = root / "state"
            with (
                patch.object(debug_lab, "RUNS_ROOT", runs),
                patch.object(debug_lab, "STATE_ROOT", state),
                patch.object(debug_lab, "_publish_latest"),
                patch("builtins.print"),
            ):
                debug_lab.run_coordinated()

            run_dirs = list(runs.iterdir())
            self.assertEqual(len(run_dirs), 1)
            summary = json.loads(
                (run_dirs[0] / "fanout/fanout_summary.json").read_text(encoding="utf-8")
            )
            contract = json.loads(
                (run_dirs[0] / "scenario_contract.json").read_text(encoding="utf-8")
            )

        self.assertEqual(summary["status"], "passed")
        self.assertEqual(
            summary["batches"],
            [["pricing-policy", "shipping-policy"], ["edge-case-verifier"]],
        )
        self.assertEqual(summary["metrics"]["completed_count"], 3)
        self.assertEqual(summary["final_decision"], "PASS")
        self.assertEqual(
            [result["touched_files"] for result in summary["results"]],
            [["pricing.py"], ["shipping.py"], []],
        )
        self.assertEqual(len(contract["cases"]), 5)
        self.assertIn("cannot run", contract["integration_gate"])

    def test_reading_scopes_separate_main_path_and_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            installed = install_reading_scopes(Path(tmp))
            self.assertEqual(len(installed), len(READING_SCOPES))
            scopes = {
                scope.get("name"): scope.get("pattern")
                for path in installed
                if (scope := ET.parse(path).getroot().find("scope")) is not None
            }

        main_path = str(scopes["00 NanoHarness Review Path"])
        extended_flows = str(scopes["05 NanoHarness Extended Flows"])
        production = str(scopes["10 NanoHarness Production Code"])
        tests = str(scopes["90 NanoHarness Tests"])
        self.assertIn("agent_forge/harness.py", main_path)
        self.assertNotIn("tests", main_path)
        self.assertIn("multi_agent/application/live_fanout.py", extended_flows)
        self.assertIn("context/application/compaction.py", extended_flows)
        self.assertIn("agent_forge/operator_console", extended_flows)
        self.assertIn("agent_forge/bench", extended_flows)
        self.assertNotIn("tests", extended_flows)
        self.assertNotIn("tests", production)
        self.assertEqual(tests, "file:tests//*")

    def test_shared_configs_route_to_one_debug_lab_in_order(self) -> None:
        expected = (
            (
                "NanoHarness Lab 1 - Governed Repair",
                "governed",
                "$PROJECT_DIR$/examples/debug_lab/run.py",
                "governed --interactive --open-workbench",
            ),
            (
                "NanoHarness Lab 2 - Coordinated Agents",
                "coordinated",
                "$PROJECT_DIR$/examples/debug_lab/run.py",
                "coordinated --open-workbench",
            ),
            (
                "NanoHarness Evidence Workbench - Read Only",
                "workbench",
                "$PROJECT_DIR$/examples/debug_lab/run.py",
                "workbench",
            ),
        )
        actual: list[tuple[str, str]] = []
        for name, scenario, script, parameters in expected:
            path = PROJECT_ROOT / ".run" / f"{name}.run.xml"
            self.assertTrue(path.is_file())
            config = ET.parse(path).getroot().find("configuration")
            self.assertIsNotNone(config)
            assert config is not None
            options = {
                option.get("name"): option.get("value")
                for option in config.findall("option")
            }
            environment = {
                variable.get("name"): variable.get("value")
                for variable in config.findall("./envs/env")
            }
            self.assertEqual(options["SCRIPT_NAME"], script)
            self.assertEqual(options["PARAMETERS"], parameters)
            self.assertEqual(options["SDK_HOME"], "$PROJECT_DIR$/.venv/bin/python")
            self.assertEqual(environment["NANOHARNESS_DEBUG_LAB"], scenario)
            if scenario == "governed":
                self.assertEqual(options["EMULATE_TERMINAL"], "true")
            actual.append((str(config.get("name")), scenario))
        self.assertEqual(
            actual,
            [(name, scenario) for name, scenario, _, _ in expected],
        )

    def test_shared_run_configuration_catalog_stays_small(self) -> None:
        expected = {
            "NanoHarness Benchmark - Inspect SWE-bench Case.run.xml",
            "NanoHarness Benchmark - SWE-bench Verified Mini 50.run.xml",
            "NanoHarness Lab 1 - Governed Repair.run.xml",
            "NanoHarness Lab 2 - Coordinated Agents.run.xml",
            "NanoHarness Evidence Workbench - Read Only.run.xml",
        }
        actual = {path.name for path in (PROJECT_ROOT / ".run").glob("*.run.xml")}

        self.assertEqual(actual, expected)

    def test_canonical_debug_lab_guide_only_presents_active_entries(self) -> None:
        guide = (PROJECT_ROOT / "examples" / "debug_lab" / "README.md").read_text(
            encoding="utf-8"
        )
        for name in (
            "NanoHarness Lab 1 - Governed Repair",
            "NanoHarness Lab 2 - Coordinated Agents",
            "NanoHarness Evidence Workbench - Read Only",
            "Pause",
            "Cancel",
            "不可变 Run",
        ):
            self.assertIn(name, guide)
        for removed in (
            "NanoHarness Inspect Latest",
            "NanoHarness Legacy Live Control",
            "NanoHarness Legacy Deterministic Control",
            "NanoHarness Legacy Demo",
            "single-live",
            "official-rerun",
        ):
            self.assertNotIn(removed, guide)
        self.assertFalse(
            (PROJECT_ROOT / "examples" / "debug_lab" / "RUN_CONFIGURATIONS.md").exists()
        )
        self.assertFalse(
            (PROJECT_ROOT / "examples" / "debug_lab" / "MASTERY_SCORECARD.md").exists()
        )

    def test_breakpoint_symbols_resolve_and_install_idempotently(self) -> None:
        resolved = resolve_breakpoints(PROJECT_ROOT)
        self.assertEqual(len(resolved), 12)
        self.assertEqual(len(resolved), len(TARGETS))
        self.assertEqual(
            len({(item["url"], item["line"], item["scenario"]) for item in resolved}),
            len(TARGETS),
        )
        self.assertEqual(
            {
                scenario: sum(item["scenario"] == scenario for item in resolved)
                for scenario in ("governed", "coordinated")
            },
            {"governed": 7, "coordinated": 5},
        )
        for item in resolved:
            self.assertIn("NANOHARNESS_DEBUG_LAB", str(item["condition"]))
            self.assertIn(str(item["scenario"]), str(item["condition"]))

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".idea" / "workspace.xml"
            workspace.parent.mkdir(parents=True)
            workspace.write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
<project version="4">
  <component name="KeepMe"><option name="value" value="yes" /></component>
  <component name="XDebuggerManager">
    <breakpoint-manager><breakpoints>
      <line-breakpoint enabled="true" suspend="THREAD" type="python-line">
        <url>file://$PROJECT_DIR$/user_file.py</url><line>4</line>
        <condition expression="keep_user_condition" language="Python" />
        <option name="timeStamp" value="40" />
      </line-breakpoint>
    </breakpoints></breakpoint-manager>
  </component>
</project>
""",
                encoding="utf-8",
            )
            install_breakpoints(PROJECT_ROOT, workspace)
            install_breakpoints(PROJECT_ROOT, workspace)
            tree = ET.parse(workspace)
            nodes = tree.getroot().findall(
                "./component[@name='XDebuggerManager']/breakpoint-manager/"
                "breakpoints/line-breakpoint"
            )
            managed = [node for node in nodes if node.findtext("group") == LAB_GROUP]
            user = [node for node in nodes if node.findtext("group") != LAB_GROUP]

        self.assertEqual(len(managed), len(TARGETS))
        self.assertTrue(
            all(
                node.find("condition") is not None
                and "NANOHARNESS_DEBUG_LAB"
                in str(node.find("condition").get("expression"))
                for node in managed
            )
        )
        self.assertEqual(len(user), 1)
        self.assertEqual(
            user[0].find("condition").get("expression"),
            "keep_user_condition",
        )
        self.assertEqual(
            tree.getroot().find("./component[@name='KeepMe']").get("name"), "KeepMe"
        )

    def test_fixtures_and_workbench_entry_do_not_duplicate_runtime(self) -> None:
        fixture = PROJECT_ROOT / "examples" / "debug_lab" / "repository"
        fanout_fixture = (
            PROJECT_ROOT / "examples" / "debug_lab" / "multi_agent_repository"
        )
        self.assertIn(
            "return a - b", (fixture / "calculator.py").read_text(encoding="utf-8")
        )
        self.assertIn(
            "assert add(2, 3) == 5",
            (fixture / "test_calculator.py").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "return subtotal",
            (fanout_fixture / "pricing.py").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "unsupported region",
            (fanout_fixture / "test_checkout.py").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "expedited=True",
            (fanout_fixture / "test_checkout.py").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "== 85",
            (fanout_fixture / "test_checkout.py").read_text(encoding="utf-8"),
        )
        showcase_script = (PROJECT_ROOT / "scripts" / "showcase_demo.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("examples/debug_lab/run.py", showcase_script)
        self.assertIn('"${scenario}" --no-open-workbench', showcase_script)
        self.assertIn("workbench_source_sha256", showcase_script)
        self.assertIn("kill -0", showcase_script)
        self.assertIn(
            'workbench_url="http://127.0.0.1:${port}/?focus=1"', showcase_script
        )
        self.assertNotIn("build=${expected_workbench_source:0:12}", showcase_script)
        self.assertIn('open -a "Google Chrome"', showcase_script)
        self.assertNotIn("forge run", showcase_script)
        self.assertNotIn("calculator.py", showcase_script)
        self.assertNotIn("--live", showcase_script)
        self.assertNotIn("--show-live", showcase_script)
        self.assertNotIn("--show-official", showcase_script)
        self.assertNotIn("--show-complex", showcase_script)
        self.assertIn("--serve", showcase_script)

        debug_entry = (PROJECT_ROOT / "examples" / "debug_lab" / "run.py").read_text(
            encoding="utf-8"
        )
        for removed_scenario in (
            "single-live",
            "official-rerun",
            "show-live",
            "show-official",
        ):
            self.assertNotIn(removed_scenario, debug_entry)

    def test_read_only_workbench_entry_does_not_run_a_lab(self) -> None:
        with (
            patch.object(sys, "argv", ["run.py", "workbench"]),
            patch.object(debug_lab, "run_governed") as run_governed,
            patch.object(debug_lab, "run_coordinated") as run_coordinated,
            patch.object(
                debug_lab,
                "_open_published_evidence_in_workbench",
            ) as open_workbench,
        ):
            debug_lab.main()

        run_governed.assert_not_called()
        run_coordinated.assert_not_called()
        open_workbench.assert_called_once_with("", stay_attached=True)

    @patch("examples.debug_lab.run.subprocess.run")
    def test_scripted_scenarios_open_their_matching_workbench_scene(
        self,
        run_process,
    ) -> None:
        expected = {
            "governed": "--show-governed",
            "coordinated": "--show-coordinated",
        }
        for scenario, flag in expected.items():
            with self.subTest(scenario=scenario):
                debug_lab._open_published_evidence_in_workbench(scenario)
                run_process.assert_called_with(
                    [str(debug_lab.WORKBENCH_LAUNCHER), flag],
                    cwd=debug_lab.PROJECT_ROOT,
                    check=True,
                )

        self.assertEqual(run_process.call_count, 2)

        debug_lab._open_published_evidence_in_workbench(
            "",
            stay_attached=True,
        )
        run_process.assert_called_with(
            [str(debug_lab.WORKBENCH_LAUNCHER), "--serve"],
            cwd=debug_lab.PROJECT_ROOT,
            check=True,
        )

    def test_benchmark_support_accepts_any_ready_docker_compatible_daemon(self) -> None:
        completed = unittest.mock.Mock(returncode=0)
        with (
            patch.object(
                debug_support.shutil,
                "which",
                return_value="/usr/local/bin/docker",
            ),
            patch.object(
                debug_support.subprocess,
                "run",
                return_value=completed,
            ) as run,
        ):
            debug_support.ensure_docker()

        run.assert_called_once_with(
            ["docker", "info"],
            check=False,
            capture_output=True,
        )


if __name__ == "__main__":
    unittest.main()

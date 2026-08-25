import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

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
    def test_reading_scopes_separate_main_path_and_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            installed = install_reading_scopes(Path(tmp))
            self.assertEqual(len(installed), len(READING_SCOPES))
            scopes = {
                scope.get("name"): scope.get("pattern")
                for path in installed
                if (scope := ET.parse(path).getroot().find("scope")) is not None
            }
        self.assertIn("multi_agent/application/fanout.py", str(scopes["00 NanoHarness Core Owners"]))
        self.assertNotIn("tests", str(scopes["01 NanoHarness All Production"]))
        self.assertEqual(scopes["20 NanoHarness Inbound Apps"], "file:apps//*")
        self.assertEqual(scopes["90 NanoHarness Tests"], "file:tests//*")

    def test_shared_run_configuration_catalog_stays_small(self) -> None:
        expected = {
            "NanoHarness Benchmark - Inspect SWE-bench Case.run.xml",
            "NanoHarness Benchmark - SWE-bench Verified Mini 50.run.xml",
            "NanoHarness Lab 1 - Governed Repair.run.xml",
            "NanoHarness Evidence Workbench - Read Only.run.xml",
            "NanoHarness Review Preflight.run.xml",
        }
        self.assertEqual(
            {path.name for path in (PROJECT_ROOT / ".run").glob("*.run.xml")},
            expected,
        )

    def test_active_run_configurations_point_to_real_entrypoints(self) -> None:
        expected = (
            (
                "NanoHarness Lab 1 - Governed Repair",
                "governed",
                "governed --interactive --open-workbench",
            ),
            (
                "NanoHarness Evidence Workbench - Read Only",
                "workbench",
                "workbench",
            ),
        )
        for name, scenario, parameters in expected:
            config = ET.parse(PROJECT_ROOT / ".run" / f"{name}.run.xml").getroot().find(
                "configuration"
            )
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
            self.assertEqual(options["SCRIPT_NAME"], "$PROJECT_DIR$/examples/debug_lab/run.py")
            self.assertEqual(options["PARAMETERS"], parameters)
            self.assertEqual(environment["NANOHARNESS_DEBUG_LAB"], scenario)

    def test_breakpoints_cover_only_durable_control(self) -> None:
        resolved = resolve_breakpoints(PROJECT_ROOT)
        self.assertEqual(len(resolved), 7)
        self.assertEqual(len(resolved), len(TARGETS))
        self.assertEqual({item["scenario"] for item in resolved}, {"governed"})
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / ".idea" / "workspace.xml"
            workspace.parent.mkdir(parents=True)
            workspace.write_text(
                "<project version='4'><component name='XDebuggerManager' /></project>",
                encoding="utf-8",
            )
            install_breakpoints(PROJECT_ROOT, workspace)
            install_breakpoints(PROJECT_ROOT, workspace)
            nodes = ET.parse(workspace).getroot().findall(
                "./component[@name='XDebuggerManager']/breakpoint-manager/"
                "breakpoints/line-breakpoint"
            )
        self.assertEqual(len(nodes), 7)
        self.assertTrue(all(node.findtext("group") == LAB_GROUP for node in nodes))

    def test_guide_points_multi_agent_to_current_runtime_smoke(self) -> None:
        guide = (PROJECT_ROOT / "examples/debug_lab/README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("run_multi_agent_v1_smoke.py", guide)
        self.assertIn("strict integration frontier", guide)
        self.assertNotIn("Historical Lab", guide)


if __name__ == "__main__":
    unittest.main()

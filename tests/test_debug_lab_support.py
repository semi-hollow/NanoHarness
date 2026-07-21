import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from examples.debug_lab import run as debug_lab
from scripts.install_pycharm_debug_lab import (
    LAB_GROUP,
    TARGETS,
    install_breakpoints,
    resolve_breakpoints,
)


PROJECT_ROOT = Path(__file__).parents[1]


class DebugLabSupportTest(unittest.TestCase):
    def test_shared_configs_route_to_one_debug_lab_in_order(self) -> None:
        expected = (
            ("NanoHarness Lab 1 - Control Plane", "control"),
            ("NanoHarness Lab 2 - Fixed Repair", "fixed"),
            ("NanoHarness Lab 3 - Live Agent", "live"),
            ("NanoHarness Lab 4 - Astropy Evidence", "astropy"),
        )
        actual: list[tuple[str, str]] = []
        for name, scenario in expected:
            path = PROJECT_ROOT / ".run" / f"{name}.run.xml"
            self.assertTrue(path.is_file())
            config = ET.parse(path).getroot().find("configuration")
            self.assertIsNotNone(config)
            assert config is not None
            options = {
                option.get("name"): option.get("value")
                for option in config.findall("option")
            }
            self.assertEqual(
                options["SCRIPT_NAME"],
                "$PROJECT_DIR$/examples/debug_lab/run.py",
            )
            self.assertEqual(options["PARAMETERS"], scenario)
            self.assertEqual(options["SDK_HOME"], "$PROJECT_DIR$/.venv/bin/python")
            actual.append((str(config.get("name")), str(options["PARAMETERS"])))
        self.assertEqual(actual, list(expected))

    def test_breakpoint_symbols_resolve_and_install_idempotently(self) -> None:
        resolved = resolve_breakpoints(PROJECT_ROOT)
        self.assertEqual(len(resolved), len(TARGETS))
        self.assertEqual(len({(item["url"], item["line"]) for item in resolved}), 13)

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

        self.assertEqual(len(managed), 13)
        self.assertEqual(len(user), 1)
        self.assertEqual(
            user[0].find("condition").get("expression"),
            "keep_user_condition",
        )
        self.assertEqual(tree.getroot().find("./component[@name='KeepMe']").get("name"), "KeepMe")

    def test_fixture_and_interview_entry_do_not_duplicate_runtime(self) -> None:
        fixture = PROJECT_ROOT / "examples" / "debug_lab" / "repository"
        self.assertIn("return a - b", (fixture / "calculator.py").read_text(encoding="utf-8"))
        self.assertIn("assert add(2, 3) == 5", (fixture / "test_calculator.py").read_text(encoding="utf-8"))
        interview = (PROJECT_ROOT / "scripts" / "interview_demo.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("examples/debug_lab/run.py", interview)
        self.assertNotIn("forge run", interview)
        self.assertNotIn("calculator.py", interview)

    def test_live_lab_republishes_harness_workspace_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "fixture"
            artifact = root / ".agent_forge" / "runs" / "run-live"
            workspace.mkdir()
            artifact.mkdir(parents=True)

            def fake_forge(_: list[str]) -> None:
                pointer = workspace / ".agent_forge" / "latest" / "run.txt"
                pointer.parent.mkdir(parents=True)
                pointer.write_text(str(artifact), encoding="utf-8")

            with (
                patch.object(debug_lab, "PROJECT_ROOT", root),
                patch.object(debug_lab, "STATE_ROOT", root / ".agent_forge" / "debug-lab"),
                patch.object(debug_lab, "RUNS_ROOT", root / ".agent_forge" / "runs"),
                patch.object(debug_lab, "_load_or_store_deepseek_key"),
                patch.object(debug_lab, "_new_workspace", return_value=workspace),
                patch.object(debug_lab, "_forge_main", side_effect=fake_forge),
            ):
                debug_lab.run_live()

            published = root / ".agent_forge" / "latest" / "run.txt"
            remembered = root / ".agent_forge" / "debug-lab" / "state" / "live_artifact.txt"
            self.assertEqual(published.read_text(encoding="utf-8"), str(artifact.resolve()))
            self.assertEqual(remembered.read_text(encoding="utf-8"), str(artifact.resolve()))


if __name__ == "__main__":
    unittest.main()

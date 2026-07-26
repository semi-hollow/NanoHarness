import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = PROJECT_ROOT / ".run"


def _options(path: Path) -> tuple[str, dict[str, str]]:
    configuration = ET.parse(path).getroot().find("configuration")
    if configuration is None:
        raise AssertionError(f"missing configuration: {path}")
    return (
        configuration.attrib["name"],
        {
            option.attrib["name"]: option.attrib.get("value", "")
            for option in configuration.findall("option")
        },
    )


class WindowsOfflineDemoTest(unittest.TestCase):
    def test_pycharm_buttons_use_the_windows_venv_and_offline_entrypoints(self):
        expected = {
            "NanoHarness Windows Offline 1 - Control Plane.run.xml": (
                "examples/debug_lab/run.py",
                "control",
                "false",
            ),
            "NanoHarness Windows Offline 2 - Fixed Repair.run.xml": (
                "examples/debug_lab/run.py",
                "fixed",
                "false",
            ),
            "NanoHarness Windows Offline 3 - Workbench.run.xml": (
                "agent_forge",
                "ui",
                "true",
            ),
        }

        for filename, (script, parameters, module_mode) in expected.items():
            with self.subTest(filename=filename):
                name, options = _options(RUN_DIR / filename)
                self.assertTrue(name.startswith("NanoHarness Windows Offline "))
                self.assertEqual(
                    options["SDK_HOME"],
                    "$PROJECT_DIR$/.venv-win/Scripts/python.exe",
                )
                self.assertTrue(options["SCRIPT_NAME"].endswith(script))
                self.assertEqual(options["PARAMETERS"], parameters)
                self.assertEqual(options["MODULE_MODE"], module_mode)

    def test_double_click_setup_reuses_the_supported_installer(self):
        cmd = (PROJECT_ROOT / "scripts" / "setup_windows_demo.cmd").read_text(
            encoding="utf-8"
        )
        script = (
            PROJECT_ROOT / "scripts" / "setup_windows_demo.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("setup_windows_demo.ps1", cmd)
        self.assertIn("setup_windows_local.ps1", script)
        self.assertIn("WithDev = $true", script)
        self.assertIn('"control"', script)
        self.assertIn('"fixed"', script)
        self.assertNotIn("DEEPSEEK_API_KEY", script)


if __name__ == "__main__":
    unittest.main()

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
        production = str(scopes["10 NanoHarness Production Code"])
        tests = str(scopes["90 NanoHarness Tests"])
        self.assertIn("agent_forge/harness.py", main_path)
        self.assertNotIn("tests", main_path)
        self.assertNotIn("tests", production)
        self.assertEqual(tests, "file:tests//*")

    def test_shared_configs_route_to_one_debug_lab_in_order(self) -> None:
        expected = (
            ("NanoHarness Lab 1 - Governed Repair", "governed"),
            ("NanoHarness Lab 2 - Coordinated Agents", "coordinated"),
            ("NanoHarness Lab 3 - Evaluation Loop", "evaluation"),
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
            self.assertEqual(
                options["PARAMETERS"],
                f"{scenario} --open-workbench",
            )
            self.assertEqual(options["SDK_HOME"], "$PROJECT_DIR$/.venv/bin/python")
            actual.append((str(config.get("name")), scenario))
        self.assertEqual(actual, list(expected))

    def test_shared_run_configuration_catalog_stays_small(self) -> None:
        expected = {
            "NanoHarness Lab 1 - Governed Repair.run.xml",
            "NanoHarness Lab 2 - Coordinated Agents.run.xml",
            "NanoHarness Lab 3 - Evaluation Loop.run.xml",
            "NanoHarness Operator Console.run.xml",
        }
        actual = {path.name for path in (PROJECT_ROOT / ".run").glob("*.run.xml")}

        self.assertEqual(actual, expected)

    def test_run_configuration_guide_only_presents_active_entries(self) -> None:
        guide = (
            PROJECT_ROOT / "examples" / "debug_lab" / "RUN_CONFIGURATIONS.md"
        ).read_text(encoding="utf-8")
        for name in (
            "NanoHarness Lab 1 - Governed Repair",
            "NanoHarness Lab 2 - Coordinated Agents",
            "NanoHarness Lab 3 - Evaluation Loop",
            "NanoHarness Operator Console",
        ):
            self.assertIn(name, guide)
        for removed in (
            "NanoHarness Inspect Latest",
            "NanoHarness Interview 1 - Live Control",
            "NanoHarness Interview Fallback - Deterministic Control",
            "NanoHarness Interview Demo",
        ):
            self.assertNotIn(removed, guide)
        self.assertFalse(
            (PROJECT_ROOT / "examples" / "interview_showcase.py").exists()
        )

    def test_breakpoint_symbols_resolve_and_install_idempotently(self) -> None:
        resolved = resolve_breakpoints(PROJECT_ROOT)
        self.assertEqual(len(resolved), len(TARGETS))
        self.assertEqual(
            len({(item["url"], item["line"]) for item in resolved}),
            len(TARGETS),
        )
        by_label = {str(item["label"]): item for item in resolved}
        candidate = by_label["Candidate diff"]
        candidate_line = (
            PROJECT_ROOT / "agent_forge" / "bench" / "adapters" / "case_runtime.py"
        ).read_text(encoding="utf-8").splitlines()[int(candidate["line"])]
        self.assertIn(
            "status = _run_status(candidate_diff_text, final_answer)",
            candidate_line,
        )

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
        self.assertEqual(len(user), 1)
        self.assertEqual(
            user[0].find("condition").get("expression"),
            "keep_user_condition",
        )
        self.assertEqual(tree.getroot().find("./component[@name='KeepMe']").get("name"), "KeepMe")

    def test_fixture_and_interview_entry_do_not_duplicate_runtime(self) -> None:
        fixture = PROJECT_ROOT / "examples" / "debug_lab" / "repository"
        fanout_fixture = (
            PROJECT_ROOT / "examples" / "debug_lab" / "multi_agent_repository"
        )
        self.assertIn("return a - b", (fixture / "calculator.py").read_text(encoding="utf-8"))
        self.assertIn("assert add(2, 3) == 5", (fixture / "test_calculator.py").read_text(encoding="utf-8"))
        self.assertIn(
            "return subtotal",
            (fanout_fixture / "pricing.py").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "== 85",
            (fanout_fixture / "test_checkout.py").read_text(encoding="utf-8"),
        )
        interview = (PROJECT_ROOT / "scripts" / "interview_demo.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("examples/debug_lab/run.py", interview)
        self.assertIn('"${scenario}" --no-open-workbench', interview)
        self.assertIn("workbench_source_sha256", interview)
        self.assertIn("kill -0", interview)
        self.assertIn("build=${expected_workbench_source:0:12}", interview)
        self.assertIn('open -a "Google Chrome"', interview)
        self.assertNotIn("forge run", interview)
        self.assertNotIn("calculator.py", interview)

    def test_core_labs_open_workbench_by_default_and_can_opt_out(self) -> None:
        with (
            patch.object(sys, "argv", ["run.py", "evaluation"]),
            patch.object(debug_lab, "run_evaluation") as run_evaluation,
            patch.object(
                debug_lab,
                "_open_published_evidence_in_workbench",
            ) as open_workbench,
        ):
            debug_lab.main()

        run_evaluation.assert_called_once_with()
        open_workbench.assert_called_once_with("evaluation")

        with (
            patch.object(
                sys,
                "argv",
                ["run.py", "evaluation", "--no-open-workbench"],
            ),
            patch.object(debug_lab, "run_evaluation") as run_evaluation,
            patch.object(
                debug_lab,
                "_open_published_evidence_in_workbench",
            ) as open_workbench,
        ):
            debug_lab.main()

        run_evaluation.assert_called_once_with()
        open_workbench.assert_not_called()

    @patch("examples.debug_lab.run.subprocess.run")
    def test_each_lab_opens_its_matching_workbench_scene(self, run_process) -> None:
        expected = {
            "governed": "--show-governed",
            "coordinated": "--show-coordinated",
            "evaluation": "--show-evaluation",
        }
        for scenario, flag in expected.items():
            with self.subTest(scenario=scenario):
                debug_lab._open_published_evidence_in_workbench(scenario)
                run_process.assert_called_with(
                    [str(debug_lab.WORKBENCH_LAUNCHER), flag],
                    cwd=debug_lab.PROJECT_ROOT,
                    check=True,
                )

        self.assertEqual(run_process.call_count, 3)

    def test_setup_handles_deferred_breakpoint_status_before_err_trap(self) -> None:
        setup = (PROJECT_ROOT / "scripts" / "setup_macos_local.sh").read_text(
            encoding="utf-8"
        )
        invocation = "if python scripts/install_pycharm_debug_lab.py; then"
        self.assertIn(invocation, setup)
        self.assertNotIn(
            "set +e\n  python scripts/install_pycharm_debug_lab.py",
            setup,
        )

    def test_live_lab_republishes_harness_workspace_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "fixture"
            artifact = root / ".agent_forge" / "runs" / "run-live"
            received_argv: list[str] = []
            workspace.mkdir()
            artifact.mkdir(parents=True)

            def fake_forge(argv: list[str]) -> None:
                received_argv.extend(argv)
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
            self.assertIn("--tool-routing", received_argv)
            self.assertIn("all", received_argv)
            self.assertIn("--skills", received_argv)
            self.assertIn("none", received_argv)
            model = received_argv.index("--model")
            self.assertEqual(received_argv[model + 1], "deepseek-v4-pro")
            thinking = received_argv.index("--thinking")
            self.assertEqual(received_argv[thinking + 1], "enabled")
            effort = received_argv.index("--reasoning-effort")
            self.assertEqual(received_argv[effort + 1], "max")
            self.assertEqual(
                [
                    received_argv[index + 1]
                    for index, value in enumerate(received_argv)
                    if value == "--tool"
                ],
                ["read_file", "replace_text", "diagnostics"],
            )

    def test_astropy_lab_uses_budget_sufficient_for_official_evidence(self) -> None:
        received_argv: list[str] = []

        with (
            patch.object(debug_lab, "_load_or_store_deepseek_key"),
            patch.object(debug_lab, "_ensure_docker"),
            patch.object(debug_lab, "_ensure_swebench"),
            patch.object(debug_lab, "_forge_main", side_effect=received_argv.extend),
            patch.object(debug_lab, "_remember_root_pointer"),
        ):
            debug_lab.run_astropy()

        max_steps = received_argv.index("--max-steps")
        self.assertEqual(received_argv[max_steps + 1], "16")
        model = received_argv.index("--model")
        self.assertEqual(received_argv[model + 1], "deepseek-v4-pro")
        thinking = received_argv.index("--thinking")
        self.assertEqual(received_argv[thinking + 1], "enabled")
        effort = received_argv.index("--reasoning-effort")
        self.assertEqual(received_argv[effort + 1], "max")
        self.assertIn("--evaluate", received_argv)

    def test_debug_lab_accepts_any_ready_docker_compatible_daemon(self) -> None:
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
            debug_lab._ensure_docker()

        run.assert_called_once_with(
            ["docker", "info"],
            check=False,
            capture_output=True,
        )


if __name__ == "__main__":
    unittest.main()

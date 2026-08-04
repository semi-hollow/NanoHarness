import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from examples import operator_console


class OperatorConsoleLauncherTest(unittest.TestCase):
    @patch("examples.operator_console.publish_latest")
    @patch("examples.operator_console.artifact_from_pointer")
    @patch("agent_forge.cli.dispatch.main")
    @patch("examples.operator_console.load_or_store_deepseek_key")
    def test_console_exit_publishes_its_workspace_run_for_button_two(
        self,
        load_key,
        forge_main,
        artifact_from_pointer,
        publish_latest,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            pointer = workspace / ".agent_forge" / "latest" / "run.txt"
            pointer.parent.mkdir(parents=True)
            pointer.write_text("/tmp/run-artifact", encoding="utf-8")
            artifact = Path(tmp) / "run-artifact"
            artifact.mkdir()
            artifact_from_pointer.return_value = artifact
            with patch(
                "examples.operator_console.load_or_create_workspace",
                return_value=workspace,
            ), patch(
                "examples.operator_console.select_practice_profile",
                return_value=operator_console.PRACTICE_PROFILES[0],
            ), patch("examples.operator_console.print_operator_drill"), patch(
                "agent_forge.observability.api.refresh_run_manifest"
            ), patch("examples.operator_console.subprocess.run"):
                operator_console.main()

        argv = forge_main.call_args.args[0]
        self.assertEqual(argv[0], "console")
        self.assertIn("--no-auto-approve-writes", argv)
        load_key.assert_called_once()
        artifact_from_pointer.assert_called_once_with(pointer)
        publish_latest.assert_called_once_with(
            artifact,
            project_root=operator_console.PROJECT_ROOT,
            state_root=operator_console.STATE_ROOT,
            scenario="complex",
        )

    def test_full_auto_profile_skips_manual_write_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            with (
                patch("examples.operator_console.load_or_store_deepseek_key"),
                patch("agent_forge.cli.dispatch.main") as forge_main,
                patch(
                    "examples.operator_console.load_or_create_workspace",
                    return_value=workspace,
                ),
                patch(
                    "examples.operator_console.select_practice_profile",
                    return_value=operator_console.PRACTICE_PROFILES[3],
                ),
                patch("examples.operator_console.print_operator_drill"),
            ):
                operator_console.main()

        argv = forge_main.call_args.args[0]
        self.assertIn("--auto-approve-writes", argv)
        self.assertNotIn("--no-auto-approve-writes", argv)
        self.assertEqual(argv[argv.index("--max-steps") + 1], "40")
        self.assertIn("--runtime-instructions", argv)
        exposed_tools = [
            argv[index + 1]
            for index, argument in enumerate(argv[:-1])
            if argument == "--tool"
        ]
        self.assertIn("python_validation", exposed_tools)
        self.assertNotIn("ask_human", exposed_tools)


if __name__ == "__main__":
    unittest.main()

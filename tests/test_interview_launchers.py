import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from examples import operator_console


class InterviewLauncherTest(unittest.TestCase):
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
            artifact_from_pointer.return_value = artifact
            with patch(
                "examples.operator_console.create_workspace",
                return_value=workspace,
            ):
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
            scenario="live",
        )


if __name__ == "__main__":
    unittest.main()

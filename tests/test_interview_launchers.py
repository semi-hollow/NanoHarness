import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from examples import interview_showcase, multi_agent_showcase, operator_console


class InterviewLauncherTest(unittest.TestCase):
    @patch("examples.interview_showcase.subprocess.run")
    def test_latest_button_reuses_the_interview_demo_script(self, run_process):
        with patch.object(sys, "argv", ["interview_showcase.py", "latest"]):
            interview_showcase.main()

        run_process.assert_called_once_with(
            [
                str(interview_showcase.DEMO_SCRIPT),
                "--show-latest",
            ],
            cwd=interview_showcase.PROJECT_ROOT,
            check=True,
        )

    @patch("examples.multi_agent_showcase.subprocess.run")
    @patch("agent_forge.cli.dispatch.main")
    @patch("examples.multi_agent_showcase.load_or_store_deepseek_key")
    @patch("examples.multi_agent_showcase.os.chdir")
    def test_multi_agent_button_runs_two_worker_fanout_then_opens_latest(
        self,
        _chdir,
        load_key,
        forge_main,
        run_process,
    ):
        multi_agent_showcase.main()

        argv = forge_main.call_args.args[0]
        self.assertEqual(argv[:2], ["run", "并行审查 Runtime 与 Safety 边界，不修改文件"])
        self.assertEqual(argv[argv.index("--agent-mode") + 1], "fanout")
        self.assertEqual(argv[argv.index("--max-workers") + 1], "2")
        self.assertEqual(argv[argv.index("--approval-mode") + 1], "locked")
        self.assertIn("--no-keep-worktree", argv)
        load_key.assert_called_once()
        run_process.assert_called_once_with(
            [
                str(
                    multi_agent_showcase.PROJECT_ROOT
                    / "scripts"
                    / "interview_demo.sh"
                ),
                "--show-latest",
            ],
            cwd=multi_agent_showcase.PROJECT_ROOT,
            check=True,
        )

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

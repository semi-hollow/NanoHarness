import tempfile
import unittest
import subprocess
import os
from pathlib import Path

from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.run_command import (
    COMMAND_TIMEOUT_SECONDS,
    MAX_COMMAND_OUTPUT_CHARS,
    RunCommandTool,
)


class RunCommandToolTest(unittest.TestCase):
    def test_custom_timeout_is_used_for_validation_commands(self):
        class Environment:
            def __init__(self):
                self.calls = []

            def execute_command(self, argv, timeout):
                self.calls.append((argv, timeout))
                return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            environment = Environment()
            tool = RunCommandTool(
                WorkspaceSandbox(tmp),
                execution_environment=environment,
                timeout_seconds=600,
            )

            observation = tool.execute({"command": "python -m compileall ."})

        self.assertTrue(observation.success)
        self.assertEqual(environment.calls[0][1], 600)

    def test_rejects_non_positive_timeout(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaisesRegex(ValueError, "timeout_seconds must be positive"),
        ):
            RunCommandTool(WorkspaceSandbox(tmp), timeout_seconds=0)

    def test_delegates_allowed_command_to_execution_environment(self):
        class Environment:
            def __init__(self):
                self.calls = []

            def execute_command(self, argv, timeout):
                self.calls.append((argv, timeout))
                return subprocess.CompletedProcess(
                    argv, 0, stdout="container ok", stderr=""
                )

        with tempfile.TemporaryDirectory() as tmp:
            environment = Environment()
            tool = RunCommandTool(
                WorkspaceSandbox(tmp), execution_environment=environment
            )

            observation = tool.execute({"command": "python -m unittest discover tests"})

        self.assertTrue(observation.success)
        self.assertEqual(
            environment.calls,
            [
                (
                    ["python", "-m", "unittest", "discover", "tests"],
                    COMMAND_TIMEOUT_SECONDS,
                )
            ],
        )

    def test_normalizes_bare_pytest_to_python_module_entrypoint(self):
        class Environment:
            def __init__(self):
                self.calls = []

            def execute_command(self, argv, timeout):
                self.calls.append((argv, timeout))
                return subprocess.CompletedProcess(
                    argv, 0, stdout="1 passed", stderr=""
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_sample.py").write_text("", encoding="utf-8")
            environment = Environment()
            tool = RunCommandTool(
                WorkspaceSandbox(root),
                execution_environment=environment,
            )

            observation = tool.execute({"command": "pytest tests/test_sample.py -v"})

        self.assertTrue(observation.success, observation.content)
        self.assertEqual(
            environment.calls,
            [
                (
                    [
                        "python",
                        "-m",
                        "pytest",
                        "--rootdir=.",
                        "-c",
                        os.devnull,
                        "tests/test_sample.py",
                        "-v",
                    ],
                    COMMAND_TIMEOUT_SECONDS,
                )
            ],
        )

    def test_reports_when_command_output_is_truncated(self):
        class Environment:
            def execute_command(self, argv, timeout):
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout="x" * (MAX_COMMAND_OUTPUT_CHARS + 1),
                    stderr="",
                )

        with tempfile.TemporaryDirectory() as tmp:
            tool = RunCommandTool(
                WorkspaceSandbox(tmp),
                execution_environment=Environment(),
            )

            observation = tool.execute({"command": "python -m compileall ."})

        self.assertTrue(observation.success)
        self.assertIn("output_truncated=true", observation.content)
        self.assertEqual(
            len(observation.content.split("\n", 1)[1]),
            MAX_COMMAND_OUTPUT_CHARS,
        )

    def test_rejects_shell_redirection_before_execution(self):
        class Environment:
            def __init__(self):
                self.calls = []

            def execute_command(self, argv, timeout):
                self.calls.append((argv, timeout))
                return subprocess.CompletedProcess(
                    argv, 0, stdout="unexpected", stderr=""
                )

        with tempfile.TemporaryDirectory() as tmp:
            environment = Environment()
            tool = RunCommandTool(
                WorkspaceSandbox(tmp),
                execution_environment=environment,
            )

            observation = tool.execute({"command": "pytest tests 2>&1"})

        self.assertFalse(observation.success)
        self.assertIn("shell operators are blocked", observation.content)
        self.assertEqual(environment.calls, [])

    def test_allows_unittest_discover_inside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_empty.py").write_text(
                "import unittest\n", encoding="utf-8"
            )
            tool = RunCommandTool(WorkspaceSandbox(root))
            observation = tool.execute({"command": "python -m unittest discover tests"})
            self.assertTrue(observation.success, observation.content)
            self.assertIn("exit_code=0", observation.content)

    def test_blocks_network_and_external_discovery_path(self):
        class Environment:
            def __init__(self):
                self.calls = []

            def execute_command(self, argv, timeout):
                self.calls.append((argv, timeout))
                return subprocess.CompletedProcess(
                    argv, 0, stdout="unexpected execution", stderr=""
                )

        with tempfile.TemporaryDirectory() as tmp:
            environment = Environment()
            tool = RunCommandTool(
                WorkspaceSandbox(tmp), execution_environment=environment
            )
            self.assertFalse(
                tool.execute({"command": "curl https://example.com"}).success
            )
            observation = tool.execute(
                {"command": "python -m unittest discover ../tests"}
            )
            self.assertFalse(observation.success)
            self.assertIn("command execution error", observation.content)
            observation = tool.execute(
                {"command": "python3 -m unittest discover ../tests"}
            )
            self.assertFalse(observation.success)
            self.assertIn("command execution error", observation.content)
            observation = tool.execute(
                {"command": "python -m unittest discover -s ../tests"}
            )
            self.assertFalse(observation.success)
            self.assertIn("command execution error", observation.content)
            observation = tool.execute({"command": "python -m compileall ../outside"})
            self.assertFalse(observation.success)
            self.assertIn("command execution error", observation.content)
            observation = tool.execute({"command": "pytest ../tests"})
            self.assertFalse(observation.success)
            self.assertIn("command execution error", observation.content)
            observation = tool.execute({"command": "pytest @../outside.args"})
            self.assertFalse(observation.success)
            self.assertIn("command execution error", observation.content)
            for command in (
                "python -m unittest discover -s../tests",
                "python -m compileall -i../outside.txt",
                "pytest -c../outside.ini",
            ):
                observation = tool.execute({"command": command})
                self.assertFalse(observation.success, command)
                self.assertIn("command execution error", observation.content)
            self.assertEqual(environment.calls, [])


if __name__ == "__main__":
    unittest.main()

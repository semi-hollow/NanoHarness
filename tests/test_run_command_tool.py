import tempfile
import unittest
import subprocess
from pathlib import Path

from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.run_command import RunCommandTool


class RunCommandToolTest(unittest.TestCase):
    def test_delegates_allowed_command_to_execution_environment(self):
        class Environment:
            def __init__(self):
                self.calls = []

            def execute_command(self, argv, timeout):
                self.calls.append((argv, timeout))
                return subprocess.CompletedProcess(argv, 0, stdout="container ok", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            environment = Environment()
            tool = RunCommandTool(WorkspaceSandbox(tmp), execution_environment=environment)

            observation = tool.execute({"command": "python -m unittest discover tests"})

        self.assertTrue(observation.success)
        self.assertEqual(environment.calls, [(["python", "-m", "unittest", "discover", "tests"], 20)])

    def test_allows_unittest_discover_inside_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_empty.py").write_text("import unittest\n", encoding="utf-8")
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
                return subprocess.CompletedProcess(argv, 0, stdout="unexpected execution", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            environment = Environment()
            tool = RunCommandTool(WorkspaceSandbox(tmp), execution_environment=environment)
            self.assertFalse(tool.execute({"command": "curl https://example.com"}).success)
            observation = tool.execute({"command": "python -m unittest discover ../tests"})
            self.assertFalse(observation.success)
            self.assertIn("command execution error", observation.content)
            observation = tool.execute({"command": "python3 -m unittest discover ../tests"})
            self.assertFalse(observation.success)
            self.assertIn("command execution error", observation.content)
            observation = tool.execute({"command": "python -m unittest discover -s ../tests"})
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

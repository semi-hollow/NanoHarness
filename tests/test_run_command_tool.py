import tempfile
import unittest
from pathlib import Path

from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.run_command import RunCommandTool


class RunCommandToolTest(unittest.TestCase):
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
        with tempfile.TemporaryDirectory() as tmp:
            tool = RunCommandTool(WorkspaceSandbox(tmp))
            self.assertFalse(tool.execute({"command": "curl https://example.com"}).success)
            observation = tool.execute({"command": "python -m unittest discover ../tests"})
            self.assertFalse(observation.success)
            self.assertIn("command execution error", observation.content)


if __name__ == "__main__":
    unittest.main()

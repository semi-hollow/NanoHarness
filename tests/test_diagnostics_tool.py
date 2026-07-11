import tempfile
import unittest
import subprocess
from pathlib import Path

from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.diagnostics import DiagnosticsTool


class DiagnosticsToolTest(unittest.TestCase):
    def test_unittest_delegates_relative_target_to_execution_environment(self):
        class Environment:
            def __init__(self):
                self.calls = []

            def execute_command(self, argv, timeout):
                self.calls.append((argv, timeout))
                return subprocess.CompletedProcess(argv, 0, stdout="container tests ok", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            environment = Environment()
            tool = DiagnosticsTool(WorkspaceSandbox(root), execution_environment=environment)

            observation = tool.execute({"kind": "unittest", "target": "tests"})

        self.assertTrue(observation.success)
        self.assertEqual(
            environment.calls,
            [(["python", "-m", "unittest", "discover", "tests"], 30)],
        )

    def test_unittest_accepts_dotted_module_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "test_sample.py").write_text(
                "import unittest\n\n"
                "class SampleTest(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertEqual(1, 1)\n",
                encoding="utf-8",
            )
            tool = DiagnosticsTool(WorkspaceSandbox(root))

            observation = tool.execute({"kind": "unittest", "target": "pkg.test_sample"})

            self.assertTrue(observation.success, observation.content)
            self.assertIn("exit_code=0", observation.content)

    def test_missing_pytest_marks_validation_blocked_not_tool_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test_needs_pytest.py").write_text("import pytest\n", encoding="utf-8")
            tool = DiagnosticsTool(WorkspaceSandbox(root))

            observation = tool.execute({"kind": "unittest", "target": "test_needs_pytest.py"})

            self.assertTrue(observation.success, observation.content)
            self.assertIn("validation_blocked", observation.content)


if __name__ == "__main__":
    unittest.main()

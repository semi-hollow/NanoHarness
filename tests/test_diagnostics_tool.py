import subprocess
import tempfile
import unittest
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

    def test_unittest_file_uses_test_runner_instead_of_direct_python(self):
        class Environment:
            def __init__(self):
                self.calls = []

            def execute_command(self, argv, timeout):
                self.calls.append((argv, timeout))
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout="Ran 1 test in 0.001s\nOK",
                    stderr="",
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test_sample.py").write_text("", encoding="utf-8")
            environment = Environment()
            tool = DiagnosticsTool(
                WorkspaceSandbox(root),
                execution_environment=environment,
            )

            observation = tool.execute({"kind": "unittest", "target": "test_sample.py"})

        self.assertTrue(observation.success, observation.content)
        self.assertEqual(
            environment.calls,
            [(["python", "-m", "unittest", "test_sample.py"], 30)],
        )

    def test_pytest_delegates_exact_node_to_execution_environment(self):
        class Environment:
            def __init__(self):
                self.calls = []

            def execute_command(self, argv, timeout):
                self.calls.append((argv, timeout))
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout="1 passed in 0.02s",
                    stderr="",
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_sample.py").write_text("", encoding="utf-8")
            environment = Environment()
            tool = DiagnosticsTool(
                WorkspaceSandbox(root),
                execution_environment=environment,
            )

            observation = tool.execute(
                {"kind": "pytest", "target": "tests/test_sample.py::test_ok"}
            )

        self.assertTrue(observation.success, observation.content)
        self.assertIn(
            "validation_command=python -m pytest tests/test_sample.py::test_ok",
            observation.content,
        )
        self.assertEqual(
            environment.calls,
            [
                (
                    [
                        "python",
                        "-m",
                        "pytest",
                        "tests/test_sample.py::test_ok",
                    ],
                    120,
                )
            ],
        )

    def test_missing_pytest_marks_validation_blocked_not_tool_failure(self):
        class Environment:
            def execute_command(self, argv, timeout):
                return subprocess.CompletedProcess(
                    argv,
                    1,
                    stdout="",
                    stderr="python.exe: No module named pytest",
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test_needs_pytest.py").write_text("", encoding="utf-8")
            tool = DiagnosticsTool(
                WorkspaceSandbox(root),
                execution_environment=Environment(),
            )

            observation = tool.execute({"kind": "pytest", "target": "test_needs_pytest.py"})

            self.assertTrue(observation.success, observation.content)
            self.assertIn("validation_blocked", observation.content)

    def test_unittest_zero_collection_is_not_reported_as_a_pass(self):
        class Environment:
            def execute_command(self, argv, timeout):
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout="Ran 0 tests in 0.000s\nOK",
                    stderr="",
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test_pytest_style.py").write_text("", encoding="utf-8")
            tool = DiagnosticsTool(
                WorkspaceSandbox(root),
                execution_environment=Environment(),
            )

            observation = tool.execute(
                {"kind": "unittest", "target": "test_pytest_style.py"}
            )

        self.assertTrue(observation.success, observation.content)
        self.assertIn("validation_blocked: unittest collected 0 tests", observation.content)


if __name__ == "__main__":
    unittest.main()

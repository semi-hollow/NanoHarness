import subprocess
import tempfile
import unittest
import os
from pathlib import Path

from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.python_validation import PythonValidationTool


class PythonValidationToolTest(unittest.TestCase):
    def test_custom_timeout_is_used_for_every_validator(self):
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
            environment = Environment()
            tool = PythonValidationTool(
                WorkspaceSandbox(root),
                execution_environment=environment,
                timeout_seconds=600,
            )

            for check_type in ("compile", "unittest", "pytest"):
                observation = tool.execute(
                    {"check_type": check_type, "validation_target": "tests"}
                )
                self.assertTrue(observation.success, observation.content)

        self.assertEqual([timeout for _, timeout in environment.calls], [600, 600, 600])

    def test_rejects_non_positive_timeout(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaisesRegex(ValueError, "timeout_seconds must be positive"),
        ):
            PythonValidationTool(WorkspaceSandbox(tmp), timeout_seconds=0)

    def test_unittest_delegates_relative_target_to_execution_environment(self):
        class Environment:
            def __init__(self):
                self.calls = []

            def execute_command(self, argv, timeout):
                self.calls.append((argv, timeout))
                return subprocess.CompletedProcess(
                    argv, 0, stdout="container tests ok", stderr=""
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            environment = Environment()
            tool = PythonValidationTool(
                WorkspaceSandbox(root),
                execution_environment=environment,
            )

            observation = tool.execute(
                {"check_type": "unittest", "validation_target": "tests"}
            )

        self.assertTrue(observation.success)
        self.assertEqual(
            environment.calls,
            [(["python", "-m", "unittest", "discover", "tests"], 120)],
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
            tool = PythonValidationTool(WorkspaceSandbox(root))

            observation = tool.execute(
                {
                    "check_type": "unittest",
                    "validation_target": "pkg.test_sample",
                }
            )

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
            tool = PythonValidationTool(
                WorkspaceSandbox(root),
                execution_environment=environment,
            )

            observation = tool.execute(
                {
                    "check_type": "unittest",
                    "validation_target": "test_sample.py",
                }
            )

        self.assertTrue(observation.success, observation.content)
        self.assertEqual(
            environment.calls,
            [(["python", "-m", "unittest", "test_sample.py"], 120)],
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
            tool = PythonValidationTool(
                WorkspaceSandbox(root),
                execution_environment=environment,
            )

            observation = tool.execute(
                {
                    "check_type": "pytest",
                    "validation_target": "tests/test_sample.py::test_ok",
                }
            )

        self.assertTrue(observation.success, observation.content)
        self.assertIn(
            "validation_command=python -m pytest --rootdir=. -c "
            f"{os.devnull} tests/test_sample.py::test_ok",
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
                        "--rootdir=.",
                        "-c",
                        os.devnull,
                        "tests/test_sample.py::test_ok",
                    ],
                    120,
                )
            ],
        )

    def test_failed_assertion_is_validation_evidence_not_tool_execution_failure(self):
        class Environment:
            def execute_command(self, argv, timeout):
                return subprocess.CompletedProcess(
                    argv,
                    1,
                    stdout="1 failed in 0.02s",
                    stderr="",
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test_sample.py").write_text("", encoding="utf-8")
            tool = PythonValidationTool(
                WorkspaceSandbox(root),
                execution_environment=Environment(),
            )

            observation = tool.execute(
                {
                    "check_type": "pytest",
                    "validation_target": "test_sample.py",
                }
            )

        self.assertFalse(observation.success)
        self.assertTrue(observation.execution_succeeded)
        self.assertIn("exit_code=1", observation.content)

    def test_pytest_target_rejects_cli_flags_with_actionable_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_sample.py").write_text("", encoding="utf-8")
            tool = PythonValidationTool(WorkspaceSandbox(root))

            observation = tool.execute(
                {
                    "check_type": "pytest",
                    "validation_target": "tests/test_sample.py -v",
                }
            )

        self.assertFalse(observation.success)
        self.assertIn("invalid arguments", observation.content)
        self.assertIn("do not append pytest flags", observation.content)

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
            tool = PythonValidationTool(
                WorkspaceSandbox(root),
                execution_environment=Environment(),
            )

            observation = tool.execute(
                {
                    "check_type": "pytest",
                    "validation_target": "test_needs_pytest.py",
                }
            )

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
            tool = PythonValidationTool(
                WorkspaceSandbox(root),
                execution_environment=Environment(),
            )

            observation = tool.execute(
                {
                    "check_type": "unittest",
                    "validation_target": "test_pytest_style.py",
                }
            )

        self.assertTrue(observation.success, observation.content)
        self.assertIn(
            "validation_blocked: unittest collected 0 tests", observation.content
        )

    def test_pytest_zero_collection_requests_project_runner_fallback(self):
        class Environment:
            def execute_command(self, argv, timeout):
                return subprocess.CompletedProcess(
                    argv,
                    5,
                    stdout="no tests ran in 0.01s",
                    stderr="",
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            tool = PythonValidationTool(
                WorkspaceSandbox(root),
                execution_environment=Environment(),
            )

            observation = tool.execute(
                {
                    "check_type": "pytest",
                    "validation_target": "tests",
                }
            )

        self.assertTrue(observation.success, observation.content)
        self.assertTrue(observation.execution_succeeded)
        self.assertIn("pytest collected no tests", observation.content)
        self.assertIn("allowlisted run_command fallback", observation.content)

    def test_pytest_does_not_load_parent_repository_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            (parent / "pyproject.toml").write_text(
                "[tool.pytest.ini_options]\naddopts = '--unknown-parent-option'\n",
                encoding="utf-8",
            )
            workspace = parent / "case-workspace"
            workspace.mkdir()
            (workspace / "test_sample.py").write_text(
                "def test_ok():\n    assert True\n",
                encoding="utf-8",
            )
            tool = PythonValidationTool(WorkspaceSandbox(workspace))

            observation = tool.execute(
                {
                    "check_type": "pytest",
                    "validation_target": "test_sample.py",
                }
            )

        self.assertTrue(observation.success, observation.content)
        self.assertIn("1 passed", observation.content)


if __name__ == "__main__":
    unittest.main()

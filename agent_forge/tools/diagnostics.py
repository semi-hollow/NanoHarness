import py_compile
import shlex
import subprocess
import sys

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.execution_environment import ExecutionEnvironment
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.sandbox import WorkspaceSandbox

from .base import Tool


class DiagnosticsTool(Tool):

    name = "diagnostics"
    description = (
        "run Python compile, unittest, or strict pytest diagnostics; "
        "pytest executes python -m pytest against one workspace target without a shell"
    )

    def __init__(
        self,
        sandbox: WorkspaceSandbox,
        execution_environment: ExecutionEnvironment | None = None,
    ) -> None:

        self.sandbox = sandbox
        self.execution_environment = execution_environment

    def schema(self) -> ToolSchema:

        return {
            "name": self.name,
            "description": self.description,
            "arguments": {"kind": "str", "target": "str"},
            "required": ["kind"],
        }

    def execute(self, arguments: ToolArguments) -> Observation:

        kind = str(arguments.get("kind", "compile")).strip().lower()
        target = arguments.get("target", ".")
        if kind == "compile":
            return self._compile(target)
        if kind == "unittest":
            return self._unittest(target)
        if kind == "pytest":
            return self._pytest(target)
        return Observation(self.name, False, f"unknown diagnostics kind: {kind}")

    def _compile(self, target: str) -> Observation:

        root = self.sandbox.ensure_safe_path(target)
        if self.execution_environment is not None:
            relative = root.relative_to(self.sandbox.workspace_root).as_posix() or "."
            proc = self.execution_environment.execute_command(
                ["python", "-m", "compileall", "-q", relative],
                timeout=30,
            )
            output = (proc.stdout + proc.stderr).strip()[:3000]
            return Observation(
                self.name,
                proc.returncode == 0,
                f"exit_code={proc.returncode}\n{output or f'compile ok: {relative}'}",
            )
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        errors: list[str] = []
        for path in files:
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as exc:
                rel = path.relative_to(self.sandbox.workspace_root).as_posix()
                errors.append(f"{rel}: {exc.msg}")
        if errors:
            return Observation(self.name, False, "\n".join(errors[:20]))
        return Observation(self.name, True, f"compile ok: {len(files)} python files")

    def _unittest(self, target: str) -> Observation:

        command = self._unittest_command(target)
        if isinstance(command, Observation):
            return command
        return self._run_test_command(command, timeout=30, kind="unittest")

    def _pytest(self, target: str) -> Observation:

        command = self._pytest_command(target)
        if isinstance(command, Observation):
            return command
        return self._run_test_command(command, timeout=120, kind="pytest")

    def _run_test_command(
        self,
        command: list[str],
        *,
        timeout: int,
        kind: str,
    ) -> Observation:

        if self.execution_environment is not None:
            proc = self.execution_environment.execute_command(command, timeout=timeout)
        else:
            local_command = [sys.executable, *command[1:]] if command and command[0] == "python" else command
            proc = subprocess.run(
                local_command,
                cwd=str(self.sandbox.workspace_root),
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        output = (proc.stdout + proc.stderr).strip()[:3000]
        command_evidence = f"validation_command={shlex.join(command)}"
        lowered_output = output.lower()
        missing_pytest = kind == "pytest" and (
            "no module named pytest" in lowered_output
            or "no module named 'pytest'" in lowered_output
        )
        if missing_pytest:
            return Observation(
                self.name,
                True,
                f"{command_evidence}\n"
                "validation_blocked: pytest is not installed in this benchmark workspace; "
                "candidate patch remains unverified by focused tests.",
            )
        if kind == "unittest" and "Ran 0 tests" in output:
            return Observation(
                self.name,
                True,
                f"{command_evidence}\n"
                "validation_blocked: unittest collected 0 tests; use kind=pytest for "
                "pytest-style test files.",
            )
        return Observation(
            self.name,
            proc.returncode == 0,
            f"{command_evidence}\nexit_code={proc.returncode}\n{output}",
        )

    def _unittest_command(self, target: str) -> list[str] | Observation:

        target = (target or ".").strip() or "."
        if "/" not in target and "\\" not in target and "." in target and not target.startswith(".") and not target.endswith(".py"):
            return ["python", "-m", "unittest", target]

        resolved = self.sandbox.ensure_safe_path(target)
        if not resolved.exists() and not str(resolved).endswith(".py"):
            py_candidate = self.sandbox.ensure_safe_path(f"{target}.py")
            if py_candidate.exists():
                resolved = py_candidate
        if resolved.is_file():
            relative = resolved.relative_to(self.sandbox.workspace_root).as_posix()
            return ["python", "-m", "unittest", relative]
        relative = resolved.relative_to(self.sandbox.workspace_root).as_posix() or "."
        return ["python", "-m", "unittest", "discover", relative]

    def _pytest_command(self, target: str) -> list[str] | Observation:

        target = (target or ".").strip() or "."
        path_target, separator, node_id = target.partition("::")
        resolved = self.sandbox.ensure_safe_path(path_target)
        if not resolved.exists():
            return Observation(
                self.name,
                False,
                f"pytest target does not exist in workspace: {path_target}",
            )
        relative = resolved.relative_to(self.sandbox.workspace_root).as_posix() or "."
        if separator:
            if not node_id.strip() or "\n" in node_id or "\r" in node_id:
                return Observation(self.name, False, "invalid pytest node id")
            relative = f"{relative}::{node_id}"
        return ["python", "-m", "pytest", relative]

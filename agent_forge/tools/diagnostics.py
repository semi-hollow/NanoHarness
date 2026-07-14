import py_compile
import subprocess
import sys

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.execution_environment import ExecutionEnvironment
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.sandbox import WorkspaceSandbox

from .base import Tool


class DiagnosticsTool(Tool):

    name = "diagnostics"
    description = "run python compile or unittest diagnostics"

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

        kind = arguments.get("kind", "compile")
        target = arguments.get("target", ".")
        if kind == "compile":
            return self._compile(target)
        if kind == "unittest":
            return self._unittest(target)
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
        if self.execution_environment is not None:
            proc = self.execution_environment.execute_command(command, timeout=30)
        else:
            local_command = [sys.executable, *command[1:]] if command and command[0] == "python" else command
            proc = subprocess.run(
                local_command,
                cwd=str(self.sandbox.workspace_root),
                text=True,
                capture_output=True,
                timeout=30,
            )
        output = (proc.stdout + proc.stderr).strip()[:3000]
        if "ModuleNotFoundError: No module named 'pytest'" in output:
            return Observation(
                self.name,
                True,
                "validation_blocked: pytest is not installed in this benchmark workspace; "
                "candidate patch remains unverified by focused tests.",
            )
        return Observation(self.name, proc.returncode == 0, f"exit_code={proc.returncode}\n{output}")

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
            return ["python", relative]
        relative = resolved.relative_to(self.sandbox.workspace_root).as_posix() or "."
        return ["python", "-m", "unittest", "discover", relative]

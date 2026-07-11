import shlex
import subprocess
import sys

from agent_forge.runtime.observation import Observation
from agent_forge.safety.permission import PermissionPolicy, PermissionDecision
from .base import Tool


class RunCommandTool(Tool):
    """Run an allowed command inside the workspace and capture its output."""

    name = "run_command"
    description = (
        "run allowlisted Python validation (`unittest`, `pytest`, `compileall`) "
        "or read-only git inspection; shell operators, `cd`, and `python -c` are blocked"
    )

    def __init__(self, sandbox, auto_approve_writes=True, execution_environment=None):
        """Keep sandbox for cwd/path checks and policy for command allow/deny."""

        self.sandbox = sandbox
        self.policy = PermissionPolicy(auto_approve_writes)
        self.execution_environment = execution_environment

    def schema(self):
        """Tell the LLM this tool needs one shell-like command string."""

        return {
            "name": self.name,
            "description": self.description,
            "arguments": {"command": "str"},
        }

    def _normalize_python(self, parts: list[str]) -> list[str]:
        """Map `python` to the active interpreter so venv runs are stable."""

        if parts and parts[0] in {"python", "python3", "python3.11"}:
            return [sys.executable] + parts[1:]
        return parts

    def _validate_command_paths(self, parts: list[str]):
        """Keep allowlisted test commands from reading or executing outside the workspace."""

        normalized = list(parts)
        if normalized and normalized[0] in {"python", "python3", "python3.11", sys.executable}:
            normalized[0] = "python"

        if normalized[:4] == ["python", "-m", "unittest", "discover"]:
            self._validate_discovery_args(normalized[4:])
            return
        if normalized[:3] == ["python", "-m", "unittest"]:
            self._validate_path_like_args(normalized[3:])
            return
        if normalized[:3] == ["python", "-m", "compileall"]:
            self._validate_path_like_args(normalized[3:], treat_positionals_as_paths=True)
            return
        if normalized[:3] == ["python", "-m", "pytest"]:
            self._validate_path_like_args(normalized[3:])
            return
        if normalized and normalized[0] == "pytest":
            self._validate_path_like_args(normalized[1:])

    def _validate_discovery_args(self, args: list[str]):
        """Validate positional and option-based unittest discovery directories."""

        path_options = {"-s", "--start-directory", "-t", "--top-level-directory"}
        index = 0
        while index < len(args):
            value = args[index]
            attached_path = next(
                (value[len(option):] for option in ("-s", "-t") if value.startswith(option) and value != option),
                "",
            )
            if attached_path:
                self.sandbox.ensure_safe_path(attached_path)
                index += 1
                continue
            if value in path_options and index + 1 < len(args):
                self.sandbox.ensure_safe_path(args[index + 1])
                index += 2
                continue
            if any(value.startswith(f"{option}=") for option in path_options if option.startswith("--")):
                self.sandbox.ensure_safe_path(value.split("=", 1)[1])
            elif not value.startswith("-"):
                # Positional discover arguments are start, pattern, and top.
                # Treating all three as paths is conservative and still permits
                # normal in-workspace patterns such as ``test*.py``.
                self.sandbox.ensure_safe_path(value)
            index += 1

    def _validate_path_like_args(self, args: list[str], *, treat_positionals_as_paths: bool = False):
        """Validate path-bearing arguments accepted by unittest/pytest/compileall."""

        path_options = {
            "-c",
            "--confcutdir",
            "--rootdir",
            "--basetemp",
            "--ignore",
            "--junitxml",
            "--junit-xml",
            "-i",  # compileall file list
        }
        index = 0
        while index < len(args):
            value = args[index]
            attached_path = next(
                (value[len(option):] for option in ("-c", "-i") if value.startswith(option) and value != option),
                "",
            )
            if attached_path:
                self._ensure_safe_cli_path(attached_path)
                index += 1
                continue
            if value in path_options and index + 1 < len(args):
                self._ensure_safe_cli_path(args[index + 1])
                index += 2
                continue
            if value.startswith("--") and "=" in value:
                option, candidate = value.split("=", 1)
                if option in path_options:
                    self._ensure_safe_cli_path(candidate)
            elif not value.startswith("-") and (treat_positionals_as_paths or self._looks_like_path(value)):
                self._ensure_safe_cli_path(value)
            index += 1

    def _ensure_safe_cli_path(self, value: str):
        """Strip a pytest node id before applying the workspace path boundary."""

        candidate = value.split("::", 1)[0]
        if candidate.startswith("@"):
            candidate = candidate[1:]
        if candidate:
            self.sandbox.ensure_safe_path(candidate)

    def _looks_like_path(self, value: str) -> bool:
        """Distinguish test module names from path selectors."""

        candidate = value.split("::", 1)[0]
        if candidate.startswith("@"):
            candidate = candidate[1:]
        return (
            candidate.startswith((".", "/", "~"))
            or "/" in candidate
            or "\\" in candidate
            or candidate.endswith((".py", ".ini", ".toml", ".xml"))
        )

    def execute(self, arguments):
        """Run the command with shell=False and return stdout/stderr evidence."""

        cmd = arguments.get("command", "")
        decision, reason = self.policy.decide("run_command", cmd)
        if decision != PermissionDecision.ALLOW:
            return Observation(self.name, False, reason)

        try:
            parts = shlex.split(cmd)
            self._validate_command_paths(parts)
            if self.execution_environment is not None:
                proc = self.execution_environment.execute_command(parts, timeout=20)
            else:
                parts = self._normalize_python(parts)
                proc = subprocess.run(
                    parts,
                    cwd=str(self.sandbox.workspace_root),
                    # shell=False prevents shell injection and makes CommandPolicy's
                    # parsed executable match what subprocess actually runs.
                    shell=False,
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
            output = (proc.stdout + proc.stderr).strip()[:2000]
            return Observation(self.name, proc.returncode == 0, f"exit_code={proc.returncode}\n{output}")
        except Exception as e:
            return Observation(self.name, False, f"command execution error: {e}")

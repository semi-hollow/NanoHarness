import shlex
import subprocess
import sys

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.execution_environment import ExecutionEnvironment
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.permission import PermissionPolicy, PermissionDecision
from agent_forge.safety.sandbox import WorkspaceSandbox
from .base import Tool


class RunCommandTool(Tool):
    name = "run_command"
    description = (
        "low-level fallback for allowlisted Python validation (`unittest`, `pytest`, "
        "`compileall`) or read-only git inspection; prefer python_validation for tests; "
        "shell operators, `cd`, and `python -c` are blocked"
    )

    def __init__(
        self,
        sandbox: WorkspaceSandbox,
        auto_approve_writes: bool = True,
        execution_environment: ExecutionEnvironment | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.policy = PermissionPolicy(auto_approve_writes)
        self.execution_environment = execution_environment

    def schema(self) -> ToolSchema:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": {"command": "str"},
        }

    def _normalize_python(self, parts: list[str]) -> list[str]:
        if parts and parts[0] in {"python", "python3", "python3.11"}:
            return [sys.executable] + parts[1:]
        return parts

    @staticmethod
    def _normalize_validation_entrypoint(parts: list[str]) -> list[str]:
        """把裸 ``pytest`` 收敛成模块调用，确保 workspace 位于 import path。"""

        if parts and parts[0] == "pytest":
            return ["python", "-m", "pytest", *parts[1:]]
        return parts

    def _validate_command_paths(self, parts: list[str]) -> None:
        normalized = list(parts)
        if normalized and normalized[0] in {
            "python",
            "python3",
            "python3.11",
            sys.executable,
        }:
            normalized[0] = "python"

        if normalized[:4] == ["python", "-m", "unittest", "discover"]:
            self._validate_discovery_args(normalized[4:])
            return
        if normalized[:3] == ["python", "-m", "unittest"]:
            self._validate_path_like_args(normalized[3:])
            return
        if normalized[:3] == ["python", "-m", "compileall"]:
            self._validate_path_like_args(
                normalized[3:], treat_positionals_as_paths=True
            )
            return
        if normalized[:3] == ["python", "-m", "pytest"]:
            self._validate_path_like_args(normalized[3:])
            return
        if normalized and normalized[0] == "pytest":
            self._validate_path_like_args(normalized[1:])

    def _validate_discovery_args(self, args: list[str]) -> None:
        path_options = {"-s", "--start-directory", "-t", "--top-level-directory"}
        index = 0
        while index < len(args):
            value = args[index]
            attached_option_path = self._extract_attached_short_option_path(
                value,
                options=("-s", "-t"),
            )
            if attached_option_path:
                self.sandbox.ensure_safe_path(attached_option_path)
                index += 1
                continue
            if value in path_options and index + 1 < len(args):
                self.sandbox.ensure_safe_path(args[index + 1])
                index += 2
                continue
            if any(
                value.startswith(f"{option}=")
                for option in path_options
                if option.startswith("--")
            ):
                self.sandbox.ensure_safe_path(value.split("=", 1)[1])
            elif not value.startswith("-"):
                self.sandbox.ensure_safe_path(value)
            index += 1

    def _validate_path_like_args(
        self,
        args: list[str],
        *,
        treat_positionals_as_paths: bool = False,
    ) -> None:
        path_options = {
            "-c",
            "--confcutdir",
            "--rootdir",
            "--basetemp",
            "--ignore",
            "--junitxml",
            "--junit-xml",
            "-i",
        }
        index = 0
        while index < len(args):
            value = args[index]
            attached_option_path = self._extract_attached_short_option_path(
                value,
                options=("-c", "-i"),
            )
            if attached_option_path:
                self._ensure_safe_cli_path(attached_option_path)
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
            elif not value.startswith("-") and (
                treat_positionals_as_paths or self._looks_like_path(value)
            ):
                self._ensure_safe_cli_path(value)
            index += 1

    @staticmethod
    def _extract_attached_short_option_path(
        argument: str,
        *,
        options: tuple[str, ...],
    ) -> str:
        """从 ``-spath`` 这类短选项中取出路径；独立 ``-s`` 返回空字符串。"""

        for option in options:
            has_attached_value = argument.startswith(option) and argument != option
            if has_attached_value:
                return argument[len(option) :]
        return ""

    def _ensure_safe_cli_path(self, value: str) -> None:
        candidate = value.split("::", 1)[0]
        if candidate.startswith("@"):
            candidate = candidate[1:]
        if candidate:
            self.sandbox.ensure_safe_path(candidate)

    def _looks_like_path(self, value: str) -> bool:
        candidate = value.split("::", 1)[0]
        if candidate.startswith("@"):
            candidate = candidate[1:]
        return (
            candidate.startswith((".", "/", "~"))
            or "/" in candidate
            or "\\" in candidate
            or candidate.endswith((".py", ".ini", ".toml", ".xml"))
        )

    def execute(self, arguments: ToolArguments) -> Observation:
        cmd = str(arguments.get("command", "") or "")
        decision, reason = self.policy.decide("run_command", cmd)
        if decision != PermissionDecision.ALLOW:
            return Observation(
                tool_name=self.name,
                success=False,
                content=reason,
            )

        try:
            parts = shlex.split(cmd)
            self._validate_command_paths(parts)
            parts = self._normalize_validation_entrypoint(parts)
            if self.execution_environment is not None:
                proc = self.execution_environment.execute_command(parts, timeout=20)
            else:
                parts = self._normalize_python(parts)
                proc = subprocess.run(
                    parts,
                    cwd=str(self.sandbox.workspace_root),
                    shell=False,
                    text=True,
                    capture_output=True,
                    timeout=20,
                )
            output = (proc.stdout + proc.stderr).strip()[:2000]
            return Observation(
                tool_name=self.name,
                success=proc.returncode == 0,
                content=f"exit_code={proc.returncode}\n{output}",
            )
        except Exception as e:
            return Observation(
                tool_name=self.name,
                success=False,
                content=f"command execution error: {e}",
            )

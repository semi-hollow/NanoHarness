"""受白名单约束的验证/只读 Git 命令 Tool；不是任意 Shell。

系统角色：把模型提供的 command 解析成 argv，经 PermissionPolicy 与 workspace 路径校验后，
交给隔离 ExecutionEnvironment 执行并返回有界 Observation。
输入：单个 command string；输出：exit code + 截断输出；不支持 shell operators/cd/python -c。
相邻边界：Router 决定是否可见，Tool Pipeline 负责 Approval/Ledger，本 Tool 负责 argv/path。

折叠导航：1 schema/normalization；2 path validation；3 governed execution。
"""

import os
import shlex
import subprocess
import sys

from agent_forge.context.adapters.repository_map import invalidate_repo_map
from agent_forge.contracts import (
    DEFAULT_TOOL_EXECUTION_TIMEOUT_SECONDS,
    ToolArguments,
    ToolSchema,
)
from agent_forge.runtime.adapters.execution_environment import ExecutionEnvironment
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.permission import PermissionPolicy, PermissionDecision
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.base import Tool


COMMAND_TIMEOUT_SECONDS = DEFAULT_TOOL_EXECUTION_TIMEOUT_SECONDS
MAX_COMMAND_OUTPUT_CHARS = 6_000


class RunCommandTool(Tool):
    """执行受白名单约束的验证命令，而不是提供任意 Shell。"""

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
        timeout_seconds: int = COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.sandbox = sandbox
        self.policy = PermissionPolicy(auto_approve_writes)
        self.execution_environment = execution_environment
        self.timeout_seconds = timeout_seconds

    # region 1. Schema 与固定 validation entrypoint normalization
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

    def _normalize_validation_entrypoint(self, parts: list[str]) -> list[str]:
        """统一 pytest 入口，并隔离目标 workspace 与 NanoHarness 的配置。"""

        if parts and parts[0] == "pytest":
            parts = ["python", "-m", "pytest", *parts[1:]]
        if parts[:3] != ["python", "-m", "pytest"]:
            return parts

        pytest_args = list(parts[3:])
        if not any(
            value == "--rootdir" or value.startswith("--rootdir=")
            for value in pytest_args
        ):
            pytest_args.insert(0, "--rootdir=.")
        if not any(value == "-c" or value.startswith("-c") for value in pytest_args):
            pytest_args[1:1] = ["-c", self._pytest_config_path()]
        return ["python", "-m", "pytest", *pytest_args]

    def _pytest_config_path(self) -> str:
        """优先使用目标仓库配置；没有时禁止读取父仓库配置。"""

        for filename in ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"):
            if (self.sandbox.workspace_root / filename).is_file():
                return filename
        return os.devnull
    # endregion 1. Schema 与 normalization 结束

    # region 2. Path validation：命令能运行不代表参数可以逃出 workspace
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
        # unittest discovery 同时存在独立值和 attached short option 两种路径形状。
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
        # pytest/compile 参数逐项扫描；已知 path option 与 path-like positional 都受 Sandbox 约束。
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
    # endregion 2. Path validation 结束

# region 3. 受治理执行：Policy -> argv -> Environment -> Observation
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
            # shell=False 前仍先 shlex 解析与路径检查；ExecutionEnvironment 只接收 argv。
            parts = shlex.split(cmd)
            self._validate_command_paths(parts)
            parts = self._normalize_validation_entrypoint(parts)
            if self.execution_environment is not None:
                proc = self.execution_environment.execute_command(
                    parts,
                    timeout=self.timeout_seconds,
                )
            else:
                parts = self._normalize_python(parts)
                proc = subprocess.run(
                    parts,
                    cwd=str(self.sandbox.workspace_root),
                    shell=False,
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                )
            complete_output = (proc.stdout + proc.stderr).strip()
            # 受限命令仍可能创建/删除验证辅助文件；成功后保守失效结构缓存。
            if proc.returncode == 0:
                invalidate_repo_map(self.sandbox.workspace_root)
            output_truncated = len(complete_output) > MAX_COMMAND_OUTPUT_CHARS
            visible_output = complete_output[:MAX_COMMAND_OUTPUT_CHARS]
            return Observation(
                tool_name=self.name,
                success=proc.returncode == 0,
                content=(
                    f"exit_code={proc.returncode} "
                    f"output_truncated={str(output_truncated).lower()}\n"
                    f"{visible_output}"
                ),
            )
        except Exception as e:
            return Observation(
                tool_name=self.name,
                success=False,
                content=f"command execution error: {e}",
            )
    # endregion 3. Governed execution 结束

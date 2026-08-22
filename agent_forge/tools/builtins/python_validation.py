"""模型可调用的受限 Python 验证工具。

``PythonValidationTool`` 不是 Benchmark 失败诊断器。它更像 Java 项目里只开放
``mvn test``/``mvn compile`` 的构建服务门面：模型选择允许的检查类型和 workspace
目标，Tool 再构造固定 argv、检查路径并返回统一 Observation。它不会解释任意 shell。
"""

from __future__ import annotations

import os
import py_compile
import shlex
import subprocess
import sys

from agent_forge.contracts import (
    DEFAULT_TOOL_EXECUTION_TIMEOUT_SECONDS,
    ToolArguments,
    ToolSchema,
)
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.runtime.adapters.execution_environment import ExecutionEnvironment
from agent_forge.safety.sandbox import WorkspaceSandbox

from agent_forge.tools.base import Tool


class PythonValidationTool(Tool):
    """把 compile、unittest、pytest 收敛为三种可审计的 Python 检查。"""

    name = "python_validation"
    description = (
        "validate Python code with one allowlisted check_type: compile, unittest, or pytest; "
        "validation_target is only a workspace path or path::node_id, without pytest flags "
        "such as -v; no shell is used"
    )

    def __init__(
        self,
        sandbox: WorkspaceSandbox,
        execution_environment: ExecutionEnvironment | None = None,
        timeout_seconds: int = DEFAULT_TOOL_EXECUTION_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.sandbox = sandbox
        self.execution_environment = execution_environment
        self.timeout_seconds = timeout_seconds

    def schema(self) -> ToolSchema:
        """告诉模型唯一可选的动作和目标字段。"""

        return {
            "name": self.name,
            "description": self.description,
            "arguments": {
                "check_type": "str",
                "validation_target": "str",
            },
            "required": ["check_type"],
        }

    # 主要入口：把模型的验证意图映射到三条固定执行路径。
    def execute(self, arguments: ToolArguments) -> Observation:
        """运行一种受支持的 Python 检查，不接受任意命令文本。"""

        check_type = str(arguments.get("check_type", "compile")).strip().lower()
        validation_target = str(arguments.get("validation_target", ".") or ".")
        if check_type == "compile":
            return self._compile_python(validation_target)
        if check_type == "unittest":
            return self._run_unittest(validation_target)
        if check_type == "pytest":
            return self._run_pytest(validation_target)
        return Observation(
            tool_name=self.name,
            success=False,
            content=f"unknown Python validation check_type: {check_type}",
        )

    def _compile_python(self, validation_target: str) -> Observation:
        """只检查 Python 语法/字节码编译，不执行测试，也不证明行为正确。"""

        resolved_target = self.sandbox.ensure_safe_path(validation_target)
        if not resolved_target.exists():
            return Observation(
                tool_name=self.name,
                success=False,
                content=f"compile target does not exist in workspace: {validation_target}",
            )
        if self.execution_environment is not None:
            relative_target = (
                resolved_target.relative_to(self.sandbox.workspace_root).as_posix()
                or "."
            )
            process = self.execution_environment.execute_command(
                ["python", "-m", "compileall", "-q", relative_target],
                timeout=self.timeout_seconds,
            )
            output = (process.stdout + process.stderr).strip()[:3000]
            return Observation(
                tool_name=self.name,
                success=process.returncode == 0,
                content=(
                    f"validation_command=python -m compileall -q {relative_target}\n"
                    f"exit_code={process.returncode}\n"
                    f"{output or f'compile ok: {relative_target}'}"
                ),
                execution_succeeded=True,
            )

        python_files = (
            [resolved_target]
            if resolved_target.is_file()
            else sorted(resolved_target.rglob("*.py"))
        )
        if not python_files:
            return Observation(
                tool_name=self.name,
                success=True,
                content=(
                    "validation_blocked: compile found no Python files under "
                    f"{validation_target}"
                ),
            )
        compile_errors: list[str] = []
        for path in python_files:
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as exc:
                relative_path = path.relative_to(self.sandbox.workspace_root).as_posix()
                compile_errors.append(f"{relative_path}: {exc.msg}")
        if compile_errors:
            return Observation(
                tool_name=self.name,
                success=False,
                content="\n".join(compile_errors[:20]),
                execution_succeeded=True,
            )
        return Observation(
            tool_name=self.name,
            success=True,
            content=f"compile ok: {len(python_files)} python files",
        )

    def _run_unittest(self, validation_target: str) -> Observation:
        """用 Python 标准库 unittest 运行模块、文件或目录 discovery。"""

        command = self._build_unittest_command(validation_target)
        if isinstance(command, Observation):
            return command
        return self._run_validation_command(
            command,
            check_type="unittest",
        )

    def _run_pytest(self, validation_target: str) -> Observation:
        """用 pytest 运行一个 workspace 路径及可选 ``::node_id``。"""

        command = self._build_pytest_command(validation_target)
        if isinstance(command, Observation):
            return command
        return self._run_validation_command(
            command,
            check_type="pytest",
        )

    def _run_validation_command(
        self,
        command: list[str],
        *,
        check_type: str,
    ) -> Observation:
        """执行固定 argv，并区分测试失败与验证环境不可用。"""

        if self.execution_environment is not None:
            process = self.execution_environment.execute_command(
                command,
                timeout=self.timeout_seconds,
            )
        else:
            local_command = (
                [sys.executable, *command[1:]]
                if command and command[0] == "python"
                else command
            )
            process = subprocess.run(
                local_command,
                cwd=str(self.sandbox.workspace_root),
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                shell=False,
            )
        output = (process.stdout + process.stderr).strip()[:3000]
        command_evidence = f"validation_command={shlex.join(command)}"
        normalized_output = output.lower()
        pytest_unavailable = check_type == "pytest" and (
            "no module named pytest" in normalized_output
            or "no module named 'pytest'" in normalized_output
        )
        if pytest_unavailable:
            # Tool 已确定“环境不可验证”，但不能把它写成测试失败或测试通过。
            return Observation(
                tool_name=self.name,
                success=True,
                content=(
                    f"{command_evidence}\n"
                    "validation_blocked: pytest is not installed in this benchmark workspace; "
                    "candidate diff remains unverified by focused tests."
                ),
            )
        if check_type == "unittest" and "Ran 0 tests" in output:
            return Observation(
                tool_name=self.name,
                success=True,
                content=(
                    f"{command_evidence}\n"
                    "validation_blocked: unittest collected 0 tests; use "
                    "check_type=pytest for pytest-style test files."
                ),
            )
        if check_type == "pytest" and process.returncode == 5:
            return Observation(
                tool_name=self.name,
                success=True,
                content=(
                    f"{command_evidence}\n"
                    "validation_blocked: pytest collected no tests for this target; "
                    "inspect the repository test entrypoint and use the allowlisted "
                    "run_command fallback when project-specific flags are required."
                ),
                execution_succeeded=True,
            )
        return Observation(
            tool_name=self.name,
            success=process.returncode == 0,
            content=(f"{command_evidence}\nexit_code={process.returncode}\n{output}"),
            execution_succeeded=True,
        )

    def _build_unittest_command(
        self,
        validation_target: str,
    ) -> list[str] | Observation:
        """把模块名、测试文件或目录转换成无 shell 的 unittest argv。"""

        target = (validation_target or ".").strip() or "."
        is_dotted_module = (
            "/" not in target
            and "\\" not in target
            and "." in target
            and not target.startswith(".")
            and not target.endswith(".py")
        )
        if is_dotted_module:
            return ["python", "-m", "unittest", target]

        resolved_target = self.sandbox.ensure_safe_path(target)
        if not resolved_target.exists() and not str(resolved_target).endswith(".py"):
            python_candidate = self.sandbox.ensure_safe_path(f"{target}.py")
            if python_candidate.exists():
                resolved_target = python_candidate
        if resolved_target.is_file():
            relative_target = resolved_target.relative_to(
                self.sandbox.workspace_root
            ).as_posix()
            return ["python", "-m", "unittest", relative_target]
        relative_target = (
            resolved_target.relative_to(self.sandbox.workspace_root).as_posix() or "."
        )
        return ["python", "-m", "unittest", "discover", relative_target]

    def _build_pytest_command(
        self,
        validation_target: str,
    ) -> list[str] | Observation:
        """验证路径和 node id，再构造与父仓库配置隔离的 pytest argv。"""

        target = (validation_target or ".").strip() or "."
        path_target, separator, node_id = target.partition("::")
        resolved_target = self.sandbox.ensure_safe_path(path_target)
        if not resolved_target.exists():
            target_parts = shlex.split(path_target)
            if (
                len(target_parts) > 1
                and self.sandbox.ensure_safe_path(target_parts[0]).exists()
            ):
                return Observation(
                    tool_name=self.name,
                    success=False,
                    content=(
                        "invalid arguments: validation_target accepts only a workspace "
                        "path or path::node_id; do not append pytest flags. "
                        f"Use {target_parts[0]!r}, not {path_target!r}."
                    ),
                )
            return Observation(
                tool_name=self.name,
                success=False,
                content=f"pytest target does not exist in workspace: {path_target}",
            )
        relative_target = (
            resolved_target.relative_to(self.sandbox.workspace_root).as_posix() or "."
        )
        if separator:
            if not node_id.strip() or "\n" in node_id or "\r" in node_id:
                return Observation(
                    tool_name=self.name,
                    success=False,
                    content="invalid pytest node id",
                )
            relative_target = f"{relative_target}::{node_id}"
        return [
            "python",
            "-m",
            "pytest",
            "--rootdir=.",
            "-c",
            self._pytest_config_path(),
            relative_target,
        ]

    def _pytest_config_path(self) -> str:
        """优先复用目标仓库配置；没有时阻止 pytest 向父目录继续搜索。"""

        for filename in ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"):
            if (self.sandbox.workspace_root / filename).is_file():
                return filename
        return os.devnull

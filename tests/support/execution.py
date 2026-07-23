"""执行环境测试替身。"""

from __future__ import annotations

import subprocess
from typing import Any


class FakeOciRunner:
    """记录 OCI 命令，并返回容器测试所需的固定结果。"""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(
        self,
        command: list[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        if command[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="sha256:image-id\n", stderr=""
            )
        if command[1] == "run":
            return subprocess.CompletedProcess(
                command, 0, stdout="container-id\n", stderr=""
            )
        if command[1] == "exec":
            return subprocess.CompletedProcess(
                command, 0, stdout="tests ok\n", stderr=""
            )
        if command[1:3] == ["rm", "-f"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="container-id\n", stderr=""
            )
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="unexpected command"
        )

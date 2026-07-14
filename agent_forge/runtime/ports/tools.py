"""工具目录与执行端口。"""

from __future__ import annotations

from typing import Protocol

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation


class ToolGateway(Protocol):
    """隔离 Application 与具体本地/MCP 工具注册实现。"""

    def schemas(self) -> list[ToolSchema]:
        """返回当前运行可路由的全部工具 schema。"""

    def get(self, name: str) -> object | None:
        """检查工具是否存在，不执行工具。"""

    def execute(self, name: str, arguments: ToolArguments) -> Observation:
        """执行一次已归一化工具调用并返回 Observation。"""

"""工具注册、schema 汇总和统一调用入口。"""

from __future__ import annotations

from typing import Any

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.runtime.ports.tools import ToolGateway

from .base import Tool


class ToolRegistry(ToolGateway):
    """按名称管理 Tool，并把参数错误或异常归一化为 Observation。

    可类比 Spring 容器中的 bean registry 加统一调用门面。Router 决定哪些 schema 给
    模型看；本类只负责查找并执行已经选中的 Tool。
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self.mcp_config_report: Any | None = None

    def register(self, tool: Tool) -> None:
        """注册一个内置或 MCP Tool 实现。"""

        self._tools[tool.name] = tool

    def schemas(self) -> list[ToolSchema]:
        """返回当前已注册工具的唯一模型契约。"""

        return [tool.schema() for tool in self._tools.values()]

    def get(self, name: str) -> Tool | None:
        """按稳定工具名查找实现；找不到时返回 None。"""

        return self._tools.get(name)

    # 主要入口：校验参数并执行已注册 Tool，所有异常归一化为 Observation。
    def execute(self, name: str, arguments: ToolArguments) -> Observation:
        """按名称查找 Tool，校验必填参数和基础类型，再调用实现。

        未注册、参数不合约或执行异常都转换为失败 ``Observation``，不会向主循环抛出。
        本类不决定本轮可见性或授权；这些检查在进入 Registry 前完成。
        """

        # 1. 名称解析：只允许调用启动时已经注册的 Tool 实现。
        tool = self.get(name)
        if not tool:
            return Observation(name, False, f"unknown tool: {name}")

        # 2. 物理契约：在实现看到参数前，先校验 schema 自身和调用参数的基础形状。
        validation_error = self._validate_arguments(tool, arguments or {})
        if validation_error:
            return Observation(name, False, validation_error)

        # 3. Registry 统一执行出口：实现异常变成失败 Observation，主循环据此恢复或停止。
        try:
            return tool.execute(arguments)
        except Exception as e:
            return Observation(name, False, f"tool execution error: {e}")

    def _validate_arguments(self, tool: Tool, arguments: ToolArguments) -> str:
        """区分坏 schema 与坏调用参数；空字符串表示物理契约通过。

        schema 未声明 ``required`` 时，当前兼容规则把所有 arguments 字段视为必填；
        显式空列表才表示无必填参数。这里只检查基础类型，不替代业务授权或路径边界。
        """

        schema = tool.schema()

        # 先验证 Tool 自己暴露的 schema，避免把配置错误误报成模型参数错误。
        expected = schema.get("arguments", {})
        if not isinstance(expected, dict):
            return "invalid tool schema: arguments must be an object"
        required_value = schema.get("required")
        if required_value is None:
            required = list(expected.keys())
        elif isinstance(required_value, list) and all(
            isinstance(name, str) for name in required_value
        ):
            required = [str(name) for name in required_value]
        else:
            return "invalid tool schema: required must be a list of strings"

        # 再验证本次 ToolCall 的缺失字段和已提供字段类型；可选字段不强制出现。
        missing = [name for name in required if name not in arguments]
        if missing:
            return f"invalid arguments: missing {', '.join(missing)}"
        for name, typ in expected.items():
            if name not in arguments:
                continue
            if not self._matches_type(arguments[name], typ):
                return f"invalid arguments: {name} must be {typ}"
        return ""

    def _matches_type(self, value: Any, typ: Any) -> bool:
        if isinstance(typ, dict):
            typ = typ.get("type", "object")
        if typ in {"str", "string"}:
            return isinstance(value, str)
        if typ in {"int", "integer"}:
            return isinstance(value, int) and not isinstance(value, bool)
        if typ in {"float", "number"}:
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if typ in {"bool", "boolean"}:
            return isinstance(value, bool)
        if typ in {"list", "array"}:
            return isinstance(value, list)
        if typ in {"dict", "object"}:
            return isinstance(value, dict)
        return True

from __future__ import annotations

from typing import Any

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation

from .base import Tool


class ToolRegistry:

    def __init__(self) -> None:

        self._tools: dict[str, Tool] = {}
        self.mcp_config_report: Any | None = None

    def register(self, tool: Tool) -> None:

        self._tools[tool.name] = tool

    def schemas(self) -> list[ToolSchema]:

        return [t.schema() for t in self._tools.values()]

    def get(self, name: str) -> Tool | None:

        return self._tools.get(name)

    # 主要入口：校验参数并执行已注册 Tool，所有异常归一化为 Observation。
    def execute(self, name: str, arguments: ToolArguments) -> Observation:
        """执行已注册工具，并把参数错误和异常归一化为 Observation。"""

        tool = self.get(name)
        if not tool:

            return Observation(name, False, f"unknown tool: {name}")
        validation_error = self._validate_arguments(tool, arguments or {})
        if validation_error:

            return Observation(name, False, validation_error)
        try:
            return tool.execute(arguments)
        except Exception as e:

            return Observation(name, False, f"tool execution error: {e}")

    def _validate_arguments(self, tool: Tool, arguments: ToolArguments) -> str:

        schema = tool.schema()

        expected = schema.get("arguments", {})
        if not isinstance(expected, dict):
            return "invalid tool schema: arguments must be an object"
        required_value = schema.get("required")
        if required_value is None:
            required = list(expected.keys())
        elif isinstance(required_value, list) and all(isinstance(name, str) for name in required_value):
            required = [str(name) for name in required_value]
        else:
            return "invalid tool schema: required must be a list of strings"
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

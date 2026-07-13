from __future__ import annotations

from typing import Any

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.observation import Observation

from .base import Tool


class ToolRegistry:
    """Registry boundary between model-generated tool calls and local tools.

    Project review point: tool calling is a protocol boundary. The LLM proposes a
    name and JSON-like args; the registry verifies the tool exists, validates
    arguments, converts exceptions into Observations, and never lets raw tool
    failures escape the AgentLoop.
    """

    def __init__(self) -> None:
        """Start with an empty tool map; `cli.build_registry` fills it."""

        self._tools: dict[str, Tool] = {}
        self.mcp_config_report: Any | None = None

    def register(self, tool: Tool) -> None:
        """Expose one tool by name so the LLM can request it later."""

        # Last registration wins deliberately; tests can replace tools easily.
        self._tools[tool.name] = tool

    def schemas(self) -> list[ToolSchema]:
        """Return all tool schemas that will be included in the LLM call."""

        return [t.schema() for t in self._tools.values()]

    def get(self, name: str) -> Tool | None:
        """Look up a tool without executing it; used by guardrails first."""

        return self._tools.get(name)

    # PRIMARY ENTRYPOINT: cross the boundary from model intent to a local tool.
    def execute(self, name: str, arguments: ToolArguments) -> Observation:
        """Validate and execute one tool call, always returning ``Observation``.

        ``AgentLoop.run`` reaches concrete tools only through this method. It
        owns tool lookup, argument validation, exception normalization, and the
        single result protocol fed into recovery and the next model turn.
        """

        tool = self.get(name)
        if not tool:
            # Unknown tools are model/tool-routing failures, not Python errors.
            return Observation(name, False, f"unknown tool: {name}")
        validation_error = self._validate_arguments(tool, arguments or {})
        if validation_error:
            # Invalid arguments should feed back into recovery; the model can
            # repair them on the next turn if StepController marks retryable.
            return Observation(name, False, validation_error)
        try:
            return tool.execute(arguments)
        except Exception as e:
            # Concrete tools should usually return Observation themselves, but
            # this catch keeps one broken tool from crashing the whole agent run.
            return Observation(name, False, f"tool execution error: {e}")

    def _validate_arguments(self, tool: Tool, arguments: ToolArguments) -> str:
        """Catch missing or obviously mistyped arguments before tools run."""

        schema = tool.schema()

        # Local tools use a compact {"arguments": {"path": "str"}} shape; MCP
        # style tools can also expose JSON Schema-like dicts.
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
        """Small schema checker that keeps bad tool calls inside the loop."""

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

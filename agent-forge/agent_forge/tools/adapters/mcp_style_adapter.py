from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

from agent_forge.runtime.observation import Observation
from agent_forge.tools.base import Tool


@dataclass(frozen=True)
class MCPStyleToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolAdapter(ABC):
    @abstractmethod
    def to_tool(self) -> Tool:
        raise NotImplementedError


class MCPStyleToolAdapter(ToolAdapter):
    """Local adapter inspired by MCP tool specs; this is not the full MCP protocol."""

    def __init__(self, spec: MCPStyleToolSpec, handler: Callable[[dict[str, Any]], Any]):
        self.spec = spec
        self.handler = handler

    def to_tool(self) -> Tool:
        spec = self.spec
        handler = self.handler

        class AdaptedTool(Tool):
            name = spec.name
            description = spec.description

            def schema(self) -> dict[str, Any]:
                return {
                    "name": spec.name,
                    "description": spec.description,
                    "arguments": spec.input_schema.get("properties", {}),
                }

            def execute(self, arguments: dict[str, Any]) -> Observation:
                required = spec.input_schema.get("required", [])
                missing = [name for name in required if name not in arguments]
                if missing:
                    return Observation(spec.name, False, f"invalid arguments: missing {', '.join(missing)}")
                result = handler(arguments)
                if isinstance(result, Observation):
                    return result
                return Observation(spec.name, True, str(result))

        return AdaptedTool()

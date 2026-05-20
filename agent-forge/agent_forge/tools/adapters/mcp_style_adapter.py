from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

from agent_forge.runtime.observation import Observation
from agent_forge.tools.base import Tool


@dataclass(frozen=True)
class MCPStyleToolSpec:
    """Minimal local representation of an MCP-like tool definition."""

    # External tool name.
    name: str

    # Natural-language description shown to the LLM.
    description: str

    # JSON Schema-like input spec.
    input_schema: dict[str, Any]


class ToolAdapter(ABC):
    """Interface for converting external tool specs into local Tool objects."""

    @abstractmethod
    def to_tool(self) -> Tool:
        """Return an Agent Forge Tool implementation."""

        raise NotImplementedError


class MCPStyleToolAdapter(ToolAdapter):
    """Local adapter inspired by MCP tool specs; this is not the full MCP protocol.

    It exists to show the extension boundary: external tools can be adapted into
    the local Tool contract without changing AgentLoop.
    """

    def __init__(self, spec: MCPStyleToolSpec, handler: Callable[[dict[str, Any]], Any]):
        """Store external spec plus callable handler."""

        self.spec = spec
        self.handler = handler

    def to_tool(self) -> Tool:
        """Wrap the MCP-style spec and handler in the local Tool contract."""

        spec = self.spec
        handler = self.handler

        class AdaptedTool(Tool):
            """Concrete Tool generated from one MCP-style spec."""

            name = spec.name
            description = spec.description

            def schema(self) -> dict[str, Any]:
                """Expose MCP input properties as Agent Forge tool arguments."""

                return {
                    "name": spec.name,
                    "description": spec.description,
                    "arguments": spec.input_schema.get("properties", {}),
                }

            def execute(self, arguments: dict[str, Any]) -> Observation:
                """Validate required arguments, run handler, normalize result."""

                required = spec.input_schema.get("required", [])
                missing = [name for name in required if name not in arguments]
                if missing:
                    return Observation(spec.name, False, f"invalid arguments: missing {', '.join(missing)}")

                # Handler can return a raw value or an Observation; wrapping raw
                # values keeps adapter behavior consistent with local tools.
                result = handler(arguments)
                if isinstance(result, Observation):
                    return result
                return Observation(spec.name, True, str(result))

        return AdaptedTool()

from __future__ import annotations

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.observation import Observation


class Tool:
    """Base contract shared by every tool the agent can call.

    `AgentLoop` never talks to concrete tools directly. It receives a tool
    name from the LLM, asks `ToolRegistry` to find the matching `Tool`, then
    calls `execute`.
    """

    name: str = ""
    description: str = ""

    def schema(self) -> ToolSchema:
        """Return the minimal schema shown to the LLM before tool calling."""

        raise NotImplementedError

    def execute(self, arguments: ToolArguments) -> Observation:
        """Run the tool and return an Observation for the next loop step."""

        raise NotImplementedError

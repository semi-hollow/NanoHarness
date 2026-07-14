from __future__ import annotations

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation


class Tool:

    name: str = ""
    description: str = ""

    def schema(self) -> ToolSchema:

        raise NotImplementedError

    def execute(self, arguments: ToolArguments) -> Observation:

        raise NotImplementedError

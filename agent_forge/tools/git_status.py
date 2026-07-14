from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation

from .run_command import RunCommandTool


class GitStatusTool(RunCommandTool):

    name = "git_status"

    def schema(self) -> ToolSchema:

        return {"name": self.name, "description": "safe git status", "arguments": {}, "required": []}

    def execute(self, arguments: ToolArguments) -> Observation:

        return super().execute({"command": "git status"})

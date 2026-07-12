from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.observation import Observation

from .run_command import RunCommandTool


class GitStatusTool(RunCommandTool):
    """Specialized command tool for `git status`."""

    name = "git_status"

    def schema(self) -> ToolSchema:
        """Expose no arguments because the command is fixed."""

        return {"name": self.name, "description": "safe git status", "arguments": {}, "required": []}

    def execute(self, arguments: ToolArguments) -> Observation:
        """Reuse RunCommandTool so status gets the same policy and tracing."""

        return super().execute({"command": "git status"})

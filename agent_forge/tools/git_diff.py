import subprocess

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.git_workspace import collect_workspace_diff
from agent_forge.runtime.domain.conversation import Observation

from .run_command import RunCommandTool


class GitDiffTool(RunCommandTool):

    name = "git_diff"

    def schema(self) -> ToolSchema:

        return {"name": self.name, "description": "safe git diff", "arguments": {}, "required": []}

    def execute(self, arguments: ToolArguments) -> Observation:

        try:
            patch = collect_workspace_diff(self.sandbox.workspace_root)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return Observation(self.name, False, f"git diff failed: {exc}")
        return Observation(self.name, True, patch[:6000] or "no workspace changes")

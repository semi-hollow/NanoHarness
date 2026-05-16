from .run_command import RunCommandTool


class GitDiffTool(RunCommandTool):
    """Specialized command tool for reading the current git diff."""

    name = "git_diff"

    def schema(self):
        """Expose no arguments because the command is fixed."""

        return {"name": self.name, "description": "safe git diff", "arguments": {}, "required": []}

    def execute(self, arguments):
        """Reuse RunCommandTool so diff capture is traced like any command."""

        return super().execute({"command": "git diff"})

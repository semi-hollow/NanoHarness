from agent_forge.runtime.observation import Observation
from .base import Tool


class ReadFileTool(Tool):
    """Read a workspace file and return a bounded text preview."""

    name = "read_file"
    description = "read file"

    def __init__(self, sandbox):
        """Keep the sandbox so every path read is checked first."""

        self.sandbox = sandbox

    def schema(self):
        """Tell the LLM this tool needs a relative file path."""

        return {"name": self.name, "description": self.description, "arguments": {"path": "str"}}

    def execute(self, arguments):
        """Read the file after sandbox validation and cap content for context size."""

        path = self.sandbox.ensure_safe_path(arguments["path"])
        if not path.exists():
            return Observation(self.name, False, f"file not found: {arguments['path']}")
        text = path.read_text(encoding="utf-8")
        lines = text.count("\n") + 1
        return Observation(self.name, True, f"path={arguments['path']} lines={lines}\n{text[:2000]}")

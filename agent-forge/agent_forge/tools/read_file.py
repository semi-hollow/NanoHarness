from agent_forge.runtime.observation import Observation
from .base import Tool


class ReadFileTool(Tool):
    name = "read_file"
    description = "read file"

    def __init__(self, sandbox):
        self.sandbox = sandbox

    def schema(self):
        return {"name": self.name, "arguments": {"path": "str"}}

    def execute(self, arguments):
        p = self.sandbox.ensure_safe_path(arguments["path"])
        if not p.exists():
            return Observation(self.name, False, f"file not found: {arguments['path']}")
        txt = p.read_text(encoding="utf-8")
        lines = txt.count("\n") + 1
        return Observation(self.name, True, f"path={arguments['path']} lines={lines}\n{txt[:2000]}")

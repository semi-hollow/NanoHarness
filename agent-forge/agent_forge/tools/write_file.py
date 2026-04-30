from agent_forge.runtime.observation import Observation
from .base import Tool


class WriteFileTool(Tool):
    name = "write_file"
    description = "write file"

    def __init__(self, sandbox): self.sandbox = sandbox
    def schema(self): return {"name": self.name, "arguments": {"path": "str", "content": "str"}}
    def execute(self, arguments):
        p = self.sandbox.ensure_safe_path(arguments["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(arguments["content"], encoding="utf-8")
        return Observation(self.name, True, f"written: {arguments['path']}")

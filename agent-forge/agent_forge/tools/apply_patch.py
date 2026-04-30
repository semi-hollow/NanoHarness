from agent_forge.runtime.observation import Observation
from .base import Tool


class ApplyPatchTool(Tool):
    name = "apply_patch"
    description = "replace once"

    def __init__(self, sandbox): self.sandbox = sandbox
    def schema(self): return {"name": self.name, "arguments": {"path":"str","old":"str","new":"str"}}
    def execute(self, arguments):
        p = self.sandbox.ensure_safe_path(arguments["path"])
        txt = p.read_text(encoding="utf-8")
        if arguments["old"] not in txt:
            return Observation(self.name, False, "old text not found")
        new_txt = txt.replace(arguments["old"], arguments["new"], 1)
        p.write_text(new_txt, encoding="utf-8")
        return Observation(self.name, True, f"patched once: {arguments['path']}")

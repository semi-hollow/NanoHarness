from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.sandbox import WorkspaceSandbox

from .base import Tool

IGNORE = {".git", "__pycache__", "node_modules", "target", "dist", "build"}


class ListFilesTool(Tool):

    name = "list_files"
    description = "list files"

    def __init__(self, sandbox: WorkspaceSandbox) -> None:

        self.sandbox = sandbox

    def schema(self) -> ToolSchema:

        return {
            "name": self.name,
            "description": self.description,
            "arguments": {"path": "str"},
            "required": [],
        }

    def execute(self, arguments: ToolArguments) -> Observation:

        root = self.sandbox.ensure_safe_path(arguments.get("path", "."))
        files: list[str] = []
        for path in root.rglob("*"):
            if any(ignored in path.parts for ignored in IGNORE):
                continue
            if path.is_file():
                files.append(str(path.relative_to(self.sandbox.workspace_root)))
            if len(files) >= 200:
                break
        return Observation(self.name, True, "\n".join(files))

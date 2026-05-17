from agent_forge.runtime.observation import Observation

from .base import Tool


IGNORE = {".git", "__pycache__", "node_modules", "target", "dist", "build"}


class ListFilesTool(Tool):
    """List workspace files so the agent can discover repository structure."""

    name = "list_files"
    description = "list files"

    def __init__(self, sandbox):
        """Keep the sandbox root as the only directory this tool walks."""

        self.sandbox = sandbox

    def schema(self):
        """Expose an optional `path` argument; default is workspace root."""

        return {
            "name": self.name,
            "description": self.description,
            "arguments": {"path": "str"},
            "required": [],
        }

    def execute(self, arguments):
        """Return up to 200 files, skipping generated or dependency folders."""

        root = self.sandbox.ensure_safe_path(arguments.get("path", "."))
        files = []
        for path in root.rglob("*"):
            if any(ignored in path.parts for ignored in IGNORE):
                continue
            if path.is_file():
                files.append(str(path.relative_to(self.sandbox.workspace_root)))
            if len(files) >= 200:
                break
        return Observation(self.name, True, "\n".join(files))

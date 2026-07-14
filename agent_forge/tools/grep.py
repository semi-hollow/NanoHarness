from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.sandbox import WorkspaceSandbox

from .base import Tool


class GrepTool(Tool):
    """Search Python files for a keyword and return compact matches."""

    name = "grep"
    description = "keyword search"

    def __init__(self, sandbox: WorkspaceSandbox) -> None:
        """Keep sandbox root so search stays inside the workspace."""

        self.sandbox = sandbox

    def schema(self) -> ToolSchema:
        """Tell the LLM this tool needs one keyword."""

        return {"name": self.name, "description": self.description, "arguments": {"keyword": "str"}}

    def execute(self, arguments: ToolArguments) -> Observation:
        """Return up to 50 matching lines as file:line:text snippets."""

        keyword = arguments["keyword"]
        matches: list[str] = []
        for path in self.sandbox.workspace_root.rglob("*.py"):
            if ".git" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line_no, line in enumerate(text.splitlines(), 1):
                if keyword in line:
                    rel = path.relative_to(self.sandbox.workspace_root)
                    matches.append(f"{rel}:{line_no}:{line.strip()}")
                if len(matches) >= 50:
                    break
        return Observation(self.name, True, "\n".join(matches))


class GrepSearchTool(GrepTool):
    """Alias kept for compatibility with plans that call `grep_search`."""

    name = "grep_search"
    description = "keyword or simple substring search"

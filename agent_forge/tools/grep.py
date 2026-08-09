from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.sandbox import WorkspaceSandbox

from .base import Tool


class GrepSearchTool(Tool):
    """在工作区的 Python 文件中执行有界关键词搜索。"""

    name = "grep_search"
    description = "search Python files for an exact, case-sensitive keyword"

    def __init__(self, sandbox: WorkspaceSandbox) -> None:

        self.sandbox = sandbox

    def schema(self) -> ToolSchema:

        return {
            "name": self.name,
            "description": self.description,
            "arguments": {"keyword": "str"},
        }

    def execute(self, arguments: ToolArguments) -> Observation:

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

from typing import Any

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.sandbox import WorkspaceSandbox
from .base import Tool


class ReadFileTool(Tool):

    name = "read_file"
    description = "read file"

    def __init__(self, sandbox: WorkspaceSandbox) -> None:

        self.sandbox = sandbox

    def schema(self) -> ToolSchema:

        return {
            "name": self.name,
            "description": "read file; optional offset is 1-based line number and limit is line count",
            "arguments": {"path": "str", "offset": "any", "limit": "any"},
            "required": ["path"],
        }

    def execute(self, arguments: ToolArguments) -> Observation:

        path = self.sandbox.ensure_safe_path(arguments["path"])
        if not path.exists():
            return Observation(self.name, False, f"file not found: {arguments['path']}")
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        total_lines = len(lines)
        offset = _optional_int(arguments.get("offset"), 1)
        limit = _optional_int(arguments.get("limit"), 120)
        offset = max(1, offset)
        limit = max(1, min(limit, 240))
        start = min(offset - 1, total_lines)
        end = min(start + limit, total_lines)
        numbered = "\n".join(f"{idx + 1}: {line}" for idx, line in enumerate(lines[start:end], start=start))
        if len(numbered) > 5000:
            numbered = numbered[:5000] + "\n[truncated]"
        return Observation(
            self.name,
            True,
            f"path={arguments['path']} lines={total_lines} window={start + 1}-{end}\n{numbered}",
        )


def _optional_int(value: Any, default: int) -> int:

    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

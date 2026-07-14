from typing import Any

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.sandbox import WorkspaceSandbox
from .base import Tool


class ReadFileTool(Tool):
    """Read a workspace file and return a bounded text preview.

    Why offset/limit matter:
        Real coding tasks often identify a function around line 200+ using grep
        before reading it. Without line-window support the model keeps seeing
        only the file header, then wastes steps writing helper scripts to print
        the target range. Supporting offset/limit keeps source inspection inside
        the governed read tool instead of pushing the model toward unsafe shell
        commands or scratch files.
    """

    name = "read_file"
    description = "read file"

    def __init__(self, sandbox: WorkspaceSandbox) -> None:
        """Keep the sandbox so every path read is checked first."""

        self.sandbox = sandbox

    def schema(self) -> ToolSchema:
        """Tell the LLM this tool needs a path and optional line window."""

        return {
            "name": self.name,
            "description": "read file; optional offset is 1-based line number and limit is line count",
            "arguments": {"path": "str", "offset": "any", "limit": "any"},
            "required": ["path"],
        }

    def execute(self, arguments: ToolArguments) -> Observation:
        """Read the file after sandbox validation and cap content for context size."""

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
    """Parse optional model-provided integers without rejecting recoverable calls."""

    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

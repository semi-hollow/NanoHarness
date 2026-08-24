"""有界、可续读的 repository file Tool。

系统角色：让模型看到明确行号窗口，并准确报告字符截断与下一 offset，避免把未返回正文
误认为已经读取。
输入：workspace path + optional offset/limit；输出：带窗口元数据的 Observation。
相邻边界：Sandbox 负责路径；本 Tool 负责读取完整性；Context preview 是另一条被动输入链。

折叠导航：1 schema；2 bounded read；3 optional integer helper。
"""

from typing import Any

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.base import Tool


DEFAULT_LINE_LIMIT = 120
MAX_LINE_LIMIT = 240
MAX_CONTENT_CHARS = 5_000


class ReadFileTool(Tool):
    """读取一个可续读的行窗口，并显式报告真实可见范围。"""

    name = "read_file"
    description = "read a bounded line window from one repository file"

    def __init__(self, sandbox: WorkspaceSandbox) -> None:
        self.sandbox = sandbox

# region 1. 模型 schema
    def schema(self) -> ToolSchema:
        return {
            "name": self.name,
            "description": "read file; optional offset is 1-based line number and limit is line count",
            "arguments": {"path": "str", "offset": "any", "limit": "any"},
            "required": ["path"],
        }
    # endregion 1. Model schema 结束

    # region 2. Bounded read：真实 returned window 驱动续读
    def execute(self, arguments: ToolArguments) -> Observation:
        path = self.sandbox.ensure_safe_path(arguments["path"])
        if not path.exists():
            return Observation(
                tool_name=self.name,
                success=False,
                content=f"file not found: {arguments['path']}",
            )
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        total_lines = len(lines)
        offset = _optional_int(arguments.get("offset"), 1)
        limit = _optional_int(arguments.get("limit"), DEFAULT_LINE_LIMIT)
        offset = max(1, offset)
        limit = max(1, min(limit, MAX_LINE_LIMIT))
        start = min(offset - 1, total_lines)
        requested_end = min(start + limit, total_lines)

        # 逐行装配，记录真正返回到模型的最后一行。旧实现先声称返回到 requested_end，
        # 再按字符硬切，导致模型误以为自己已经看过实际上不可见的代码。
        rendered_lines: list[str] = []
        rendered_chars = 0
        returned_end = start
        line_content_truncated = False
        for line_index in range(start, requested_end):
            rendered_line = f"{line_index + 1}: {lines[line_index]}"
            separator_chars = 1 if rendered_lines else 0
            remaining_chars = MAX_CONTENT_CHARS - rendered_chars - separator_chars
            if remaining_chars <= 0:
                break
            if len(rendered_line) > remaining_chars:
                rendered_lines.append(rendered_line[:remaining_chars])
                rendered_chars = MAX_CONTENT_CHARS
                returned_end = line_index + 1
                line_content_truncated = True
                break
            rendered_lines.append(rendered_line)
            rendered_chars += len(rendered_line) + separator_chars
            returned_end = line_index + 1

        has_more_lines = returned_end < total_lines
        truncated = line_content_truncated or returned_end < requested_end
        returned_window = (
            f"{start + 1}-{returned_end}" if returned_end > start else "empty"
        )
        header = (
            f"path={arguments['path']} total_lines={total_lines} "
            f"returned_window={returned_window} truncated={str(truncated).lower()}"
        )
        if has_more_lines:
            header += f" next_offset={returned_end + 1}"
        if line_content_truncated:
            header += " line_content_truncated=true"
        return Observation(
            tool_name=self.name,
            success=True,
            content="\n".join([header, *rendered_lines]),
        )
    # endregion 2. Bounded read 结束


# region 3. 可选整数辅助逻辑
def _optional_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
# endregion 3. Optional integer helper 结束

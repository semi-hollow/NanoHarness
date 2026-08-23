from pathlib import Path

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.sandbox import WorkspaceSandbox

from agent_forge.tools.base import Tool

IGNORE = {
    ".agent_forge",
    ".git",
    ".idea",
    ".venv",
    "__pycache__",
    "node_modules",
    "target",
    "dist",
    "build",
}
MAX_FILES = 200


class ListFilesTool(Tool):
    """列出工作区相对路径，并明确告诉模型结果是否被截断。"""

    name = "list_files"
    description = "list repository files under an optional path"

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
        """在安全根目录下列出有界相对路径，并显式报告是否截断。"""

        # 1. Sandbox 先冻结可见根目录；不存在的目标不退化为列出整个 workspace。
        root = self.sandbox.ensure_safe_path(arguments.get("path", "."))
        if not root.exists():
            return Observation(
                tool_name=self.name,
                success=False,
                content=f"path not found: {arguments.get('path', '.')}",
            )

        # 2. 过滤运行数据和生成目录；故意多收集一个文件作为 truncated sentinel，
        # 从而区分“正好 200 个”与“至少还有更多文件”。
        candidates = [root] if root.is_file() else sorted(root.rglob("*"))
        files: list[str] = []
        for path in candidates:
            relative_to_requested_root = (
                path.relative_to(root) if path != root else Path(path.name)
            )
            if any(part in IGNORE for part in relative_to_requested_root.parts):
                continue
            if path.is_file():
                files.append(str(path.relative_to(self.sandbox.workspace_root)))
            if len(files) > MAX_FILES:
                break

        # 3. 只返回硬上限内的路径；截断时给模型明确的下一步收窄提示。
        truncated = len(files) > MAX_FILES
        visible_files = files[:MAX_FILES]
        header = f"files={len(visible_files)} truncated={str(truncated).lower()}"
        if truncated:
            header += " next=list a narrower path"
        return Observation(
            tool_name=self.name,
            success=True,
            content="\n".join([header, *visible_files]),
        )

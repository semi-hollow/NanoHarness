import shutil
from pathlib import Path
from typing import Any

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.sandbox import WorkspaceSandbox

from .base import Tool
from .rg_support import run_bounded_rg_lines


IGNORE = {
    ".git",
    ".agent_forge",
    ".idea",
    ".venv",
    "__pycache__",
    "node_modules",
    "target",
    "dist",
    "build",
}
SEARCHABLE_SUFFIXES = {
    ".cfg",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".pyi",
    ".rst",
    ".toml",
    ".ts",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
SEARCHABLE_NAMES = {"Dockerfile", "Makefile"}
DEFAULT_MAX_RESULTS = 50
HARD_MAX_RESULTS = 200


class GrepSearchTool(Tool):
    """在工作区常见文本文件中执行有界关键词搜索。"""

    name = "grep_search"
    description = (
        "search repository text files for a keyword; case-insensitive by default; "
        "optionally narrow the search with path"
    )

    def __init__(self, sandbox: WorkspaceSandbox) -> None:
        self.sandbox = sandbox

    def schema(self) -> ToolSchema:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": {
                "keyword": "str",
                "path": "str",
                "case_sensitive": "bool",
                "max_results": "int",
            },
            "required": ["keyword"],
        }

    def execute(self, arguments: ToolArguments) -> Observation:
        """通过 ``rg`` 返回有界匹配，避免 Python 全仓逐文件扫描。"""

        keyword = str(arguments.get("keyword", "") or "")
        if not keyword:
            return Observation(
                tool_name=self.name,
                success=False,
                content="keyword must not be empty",
            )

        try:
            search_root = self.sandbox.ensure_safe_path(arguments.get("path", "."))
        except PermissionError as path_error:
            return Observation(
                tool_name=self.name,
                success=False,
                content=f"search path denied: {path_error}",
            )
        if not search_root.exists():
            return Observation(
                tool_name=self.name,
                success=False,
                content=f"search path not found: {arguments.get('path', '.')}",
            )
        case_sensitive = _optional_bool(arguments.get("case_sensitive"), False)
        if search_root.is_file() and not _is_searchable(search_root):
            return Observation(
                tool_name=self.name,
                success=True,
                content=(
                    f"keyword={keyword!r} matches=0 truncated=false "
                    f"case_sensitive={str(case_sensitive).lower()}"
                ),
            )

        rg = shutil.which("rg")
        if not rg:
            return Observation(
                tool_name=self.name,
                success=False,
                content="grep_search unavailable: ripgrep (rg) is not installed",
            )

        requested_limit = _optional_int(
            arguments.get("max_results"), DEFAULT_MAX_RESULTS
        )
        result_limit = max(1, min(requested_limit, HARD_MAX_RESULTS))
        target = search_root.relative_to(self.sandbox.workspace_root).as_posix() or "."
        command = [
            rg,
            "--line-number",
            "--no-heading",
            "--color=never",
            "--fixed-strings",
            "--hidden",
            "--sort=path",
            "--max-filesize=2M",
            "--case-sensitive" if case_sensitive else "--ignore-case",
        ]
        for ignored in sorted(IGNORE):
            command.extend(["--glob", f"!**/{ignored}/**"])
        for suffix in sorted(SEARCHABLE_SUFFIXES):
            command.extend(["--glob", f"*{suffix}"])
        for filename in sorted(SEARCHABLE_NAMES):
            command.extend(["--glob", filename])
        command.extend(["--", keyword, target])

        matches, truncated, rg_error = run_bounded_rg_lines(
            command,
            cwd=self.sandbox.workspace_root,
            max_lines=result_limit,
        )
        if rg_error:
            return Observation(tool_name=self.name, success=False, content=rg_error)
        header = (
            f"keyword={keyword!r} matches={len(matches)} "
            f"truncated={str(truncated).lower()} "
            f"case_sensitive={str(case_sensitive).lower()}"
        )
        if truncated:
            header += " next=use a narrower path or a more specific keyword"
        return Observation(
            tool_name=self.name,
            success=True,
            content="\n".join([header, *matches]),
        )


def _is_searchable(path: Path) -> bool:
    return path.suffix.lower() in SEARCHABLE_SUFFIXES or path.name in SEARCHABLE_NAMES


def _optional_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return default

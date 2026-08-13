"""由 ripgrep 文件索引支持的有界文件发现工具。"""

from __future__ import annotations

import shutil
from fnmatch import fnmatch
from typing import Any

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.sandbox import WorkspaceSandbox

from .base import Tool
from .grep import IGNORE
from .rg_support import run_bounded_rg_lines

DEFAULT_MAX_RESULTS = 100
HARD_MAX_RESULTS = 400


class FindFilesTool(Tool):
    """按 glob 查找文件名；用于替代目录树漫游式定位。"""

    name = "find_files"
    description = (
        "find repository files by glob pattern using ripgrep's file index; "
        "optionally narrow the search with path"
    )

    def __init__(self, sandbox: WorkspaceSandbox) -> None:
        self.sandbox = sandbox

    def schema(self) -> ToolSchema:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": {
                "pattern": "str",
                "path": "str",
                "max_results": "int",
            },
            "required": ["pattern"],
        }

    def execute(self, arguments: ToolArguments) -> Observation:
        pattern = str(arguments.get("pattern", "") or "").strip()
        if not pattern:
            return Observation(
                tool_name=self.name,
                success=False,
                content="pattern must not be empty",
            )
        try:
            root = self.sandbox.ensure_safe_path(arguments.get("path", "."))
        except PermissionError as path_error:
            return Observation(
                tool_name=self.name,
                success=False,
                content=f"search path denied: {path_error}",
            )
        if not root.exists():
            return Observation(
                tool_name=self.name,
                success=False,
                content=f"search path not found: {arguments.get('path', '.')}",
            )
        rg = shutil.which("rg")
        if not rg:
            return Observation(
                tool_name=self.name,
                success=False,
                content="find_files unavailable: ripgrep (rg) is not installed",
            )

        requested = _optional_int(arguments.get("max_results"), DEFAULT_MAX_RESULTS)
        limit = max(1, min(requested, HARD_MAX_RESULTS))
        if root.is_file():
            relative = root.relative_to(self.sandbox.workspace_root).as_posix()
            candidates = (
                [relative]
                if fnmatch(relative, pattern) or fnmatch(root.name, pattern)
                else []
            )
        else:
            target = root.relative_to(self.sandbox.workspace_root).as_posix() or "."
            command = [rg, "--files", "--hidden", "--sort=path"]
            for ignored in sorted(IGNORE):
                command.extend(["--glob", f"!**/{ignored}/**"])
            command.extend(["--glob", pattern, "--", target])
            raw_candidates, truncated, rg_error = run_bounded_rg_lines(
                command,
                cwd=self.sandbox.workspace_root,
                max_lines=limit,
            )
            if rg_error:
                return Observation(tool_name=self.name, success=False, content=rg_error)
            candidates = []
            for line in raw_candidates:
                relative = line.strip().removeprefix("./")
                if relative:
                    candidates.append(relative)

        ordered = sorted(dict.fromkeys(candidates))
        truncated = truncated if not root.is_file() else len(ordered) > limit
        visible = ordered[:limit]
        header = (
            f"pattern={pattern!r} files={len(visible)} "
            f"truncated={str(truncated).lower()}"
        )
        if truncated:
            header += " next=use a narrower path or pattern"
        return Observation(
            tool_name=self.name,
            success=True,
            content="\n".join([header, *visible]),
        )


def _optional_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

"""有界 repository text search Tool。

系统角色：在允许的文本文件中返回带 path/line 的关键词证据，并显式报告结果是否截断；
它不承担符号解析或语义检索。
输入：keyword、可选 path/case/limit；输出：最多硬上限条匹配的 Observation。

折叠导航：1 search policy/schema；2 governed scan；3 candidate/type helpers。
"""

from pathlib import Path
from typing import Any

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.sandbox import WorkspaceSandbox

from agent_forge.tools.base import Tool


# region 1. Search policy 与 schema
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
    # endregion 1. Search policy 与 schema 结束

    # region 2. Governed scan：路径、文件类型、数量与完整性标记
    def execute(self, arguments: ToolArguments) -> Observation:
        """返回带完整性标记的匹配结果，避免模型把截断结果当成全量结果。

        伪代码：验证关键词和搜索根 -> 应用大小写/数量边界 -> 扫描候选文本
        -> 返回匹配及 truncated/next 证据。
        """

        # 1. 查询与路径必须先成立，空关键词或不存在的根目录不启动全仓库扫描。
        keyword = str(arguments.get("keyword", "") or "")
        if not keyword:
            return Observation(
                tool_name=self.name,
                success=False,
                content="keyword must not be empty",
            )

        search_root = self.sandbox.ensure_safe_path(arguments.get("path", "."))
        if not search_root.exists():
            return Observation(
                tool_name=self.name,
                success=False,
                content=f"search path not found: {arguments.get('path', '.')}",
            )

        # 2. 调用方可以收窄数量，但不能突破硬上限；大小写规则在比较前统一冻结。
        case_sensitive = _optional_bool(arguments.get("case_sensitive"), False)
        requested_limit = _optional_int(
            arguments.get("max_results"), DEFAULT_MAX_RESULTS
        )
        result_limit = max(1, min(requested_limit, HARD_MAX_RESULTS))
        comparable_keyword = keyword if case_sensitive else keyword.lower()

        # 3. 只扫描允许的文本文件和目录；故意多收集一条作为 truncated sentinel，
        # 最终仍只把 result_limit 条正文返回给模型。
        matches: list[str] = []
        for path in _candidate_files(search_root):
            relative_to_search_root = path.relative_to(search_root)
            if any(part in IGNORE for part in relative_to_search_root.parts):
                continue
            if not _is_searchable(path):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line_no, line in enumerate(text.splitlines(), 1):
                comparable_line = line if case_sensitive else line.lower()
                if comparable_keyword in comparable_line:
                    rel = path.relative_to(self.sandbox.workspace_root)
                    matches.append(f"{rel}:{line_no}:{line.strip()}")
                if len(matches) > result_limit:
                    break
            if len(matches) > result_limit:
                break

        # 4. header 是完整性证据：结果被截断时明确指导模型缩小 path/keyword。
        truncated = len(matches) > result_limit
        visible_matches = matches[:result_limit]
        header = (
            f"keyword={keyword!r} matches={len(visible_matches)} "
            f"truncated={str(truncated).lower()} "
            f"case_sensitive={str(case_sensitive).lower()}"
        )
        if truncated:
            header += " next=use a narrower path or a more specific keyword"
        return Observation(
            tool_name=self.name,
            success=True,
            content="\n".join([header, *visible_matches]),
        )
    # endregion 2. Governed scan 结束


# region 3. Candidate 与类型辅助逻辑
def _candidate_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(path for path in root.rglob("*") if path.is_file())


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
# endregion 3. Candidate/type helpers 结束

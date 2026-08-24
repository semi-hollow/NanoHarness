from pathlib import Path
from threading import Lock

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
GENERATED_NAMES = {"agent_forge_trace.json", "eval_report.md", "summary.md"}


_STRUCTURE_REVISION_LOCK = Lock()
_STRUCTURE_REVISIONS: dict[Path, int] = {}


def repository_structure_revision(root: str | Path) -> int:
    """返回进程内 workspace 结构版本；读取/改正文不会改变它。"""

    root_path = Path(root).resolve()
    with _STRUCTURE_REVISION_LOCK:
        return _STRUCTURE_REVISIONS.get(root_path, 0)


def invalidate_repo_map(root: str | Path) -> None:
    """在 create/delete/rename 成功后使所有本进程 Repo Map cache 失效。"""

    root_path = Path(root).resolve()
    with _STRUCTURE_REVISION_LOCK:
        _STRUCTURE_REVISIONS[root_path] = _STRUCTURE_REVISIONS.get(root_path, 0) + 1


def build_repo_map(root: str | Path) -> str:
    """返回工作区内可供模型发现的相对文件路径。

    忽略规则只能检查 ``root`` 以内的相对路径。Benchmark 工作区本身通常位于
    ``.agent_forge`` 目录下；若检查绝对路径，父目录中的 ``.agent_forge`` 会让整个
    仓库被误判为应忽略，最终给模型一个空 Repo Map。
    """

    root_path = Path(root).resolve()
    files: list[str] = []
    for path in root_path.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(root_path)
        if any(
            part in IGNORE or part.endswith(".egg-info")
            for part in relative_path.parts
        ):
            continue
        if _is_generated(relative_path):
            continue
        files.append(relative_path.as_posix())
    return "\n".join(sorted(files))


def _is_generated(path: Path) -> bool:
    name = path.name
    return (
        name in GENERATED_NAMES
        or name.endswith(".pyc")
        or name.endswith(".egg-info")
        or name.startswith("trace-")
        or name.endswith("_trace.json")
        or name.endswith(".pretty.json")
    )

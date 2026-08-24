"""Repository Map 的文件系统 Adapter 与进程内结构 revision cache。

系统角色：只暴露可供 Context 发现的相对文件路径，并在 create/delete/rename 后使结构
缓存失效；不读取文件正文、不做任务相关性排序。
输入：workspace；输出：稳定排序 Repo Map 与 structure revision。

折叠导航：1 revision cache；2 repository scan；3 generated-file filter。
"""

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


# region 1. 仓库结构 revision 缓存
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
# endregion 1. Structure revision cache 结束


# region 2. Repository scan：只输出 workspace-relative visible paths
def build_repo_map(root: str | Path) -> str:
    """返回工作区内可供模型发现的相对文件路径。

    忽略规则只能检查 ``root`` 以内的相对路径。Benchmark 工作区本身通常位于
    ``.agent_forge`` 目录下；若检查绝对路径，父目录中的 ``.agent_forge`` 会让整个
    仓库被误判为应忽略，最终给模型一个空 Repo Map。
    """

    root_path = Path(root).resolve()
    files: list[str] = []
    # 单次扫描依次排除目录噪音、generated 文件，再以相对路径稳定排序。
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
# endregion 2. Repository scan 结束


# region 3. 生成文件过滤
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
# endregion 3. Generated-file filter 结束

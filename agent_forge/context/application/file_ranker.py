"""用可解释的任务相关性信号，为 Context Strategy 排序仓库文件。"""

from pathlib import Path


def _terms(query: str) -> list[str]:

    return [part.lower() for part in query.replace("/", " ").replace("_", " ").replace("-", " ").split() if part]


def _looks_like_code_task(terms: list[str]) -> bool:

    code_words = {
        "add",
        "bug",
        "class",
        "def",
        "debug",
        "fix",
        "function",
        "implement",
        "method",
        "patch",
        "refactor",
        "test",
    }
    return any(term in code_words for term in terms)


def rank_files(query: str, files: list[str], root: str | Path = ".") -> list[str]:
    """按路径、正文与代码任务信号排序候选文件，不修改文件内容。

    伪代码：提取 query/code-task 信号 -> 为每个候选计算路径、正文和文件类型分数
    -> 对生成物/证据文件降权 -> 用 path 作为稳定同分顺序。
    """

    # region 1. 任务信号：归一化查询，并判断是否需要优先生产源码
    root_path = Path(root)
    terms = _terms(query)
    code_task = _looks_like_code_task(terms)
    # endregion 1. 任务信号结束

    # region 2. 候选评分：相关性加分，展示噪音与生成物降分
    def score(path: str) -> tuple[int, str]:

        lowered_path = path.lower()
        path_obj = Path(path)
        suffix = path_obj.suffix.lower()
        parts = set(path_obj.parts)
        stem_terms = _terms(path_obj.stem)
        value = 0

        # 路径命中用于快速定位可能的 owner；文件名词根命中比目录命中更强。
        for term in terms:
            if term in lowered_path:
                value += 8
            if term in stem_terms:
                value += 10

        try:
            text = (root_path / path).read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            text = ""

        # 正文只提供有上限的重复命中增益，避免大文件凭词频淹没更准确的文件名命中。
        for term in terms:
            value += min(text.count(term), 5)

        # 代码任务偏向 Python/生产源码；文档、持久化运行数据和生成报告只作为降级候选。
        if path.endswith(".py"):
            value += 4
        if "/tests/" in f"/{path}":
            value += 2
        if code_task and suffix == ".py":
            value += 4
        if code_task and ({"src", "agent_forge"} & parts):
            value += 3
        if code_task and ("docs" in parts or suffix in {".md", ".json"}):
            value -= 6
        if parts & {".agent_forge", ".venv", "__pycache__"}:
            value -= 30
        if path_obj.name in {"agent_forge_trace.json", "eval_report.md"} or path_obj.name.endswith("_trace.json"):
            value -= 10

        return (-value, path)
    # endregion 2. 候选评分结束

    # region 3. 稳定输出：负分使高相关文件排在前面，path 负责同分确定性
    return sorted(files, key=score)
    # endregion 3. 稳定输出结束

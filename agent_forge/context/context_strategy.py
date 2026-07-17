"""Repository context 的选择与只读预览策略；不调用模型、不写外部状态。"""

from dataclasses import dataclass, field
from pathlib import Path

from .contracts import ContextMemory
from .file_ranker import rank_files
from .rag import retrieve
from .token_budget import truncate_middle

ATTENTION_SINK = [

    "Follow the latest user task, not stale session history.",
    "Inspect relevant files before editing when the task depends on code.",
    "Return tool observations to the next reasoning step before deciding.",
    "Do not claim tests passed unless a validation tool actually succeeded.",
]


# 核心数据：文件、检索、working/long-term memory 的一次选择结果。
@dataclass
class ContextStrategy:
    """静态 system context 进入预算分配前的类型化候选集合。

    文件字段记录 ranking 与 preview；memory 字段明确区分 working items、working
    summary 和长期召回；topic/inherit 解释是否继承前序会话；attention、dropped 和
    budget 字段提供治理规则与可观测证据。
    """

    selected_files: list[str]
    file_previews: list[str]
    retrieved_docs: list[str]
    working_memory_items: list[str]
    working_memory_summary: str
    long_term_memory: list[str]
    topic_relation: str
    inherit_session: bool
    attention_sink: list[str] = field(default_factory=lambda: list(ATTENTION_SINK))
    dropped_context: list[str] = field(default_factory=list)
    budget_breakdown: dict[str, int] = field(default_factory=dict)


# 核心规则：按任务相关性选择文件、检索结果和可继承的记忆视图。
def build_context_strategy(
    task: str,
    files: list[str],
    docs: list[str],
    working_memory: ContextMemory,
    root: str | Path,
    max_chars: int,
) -> ContextStrategy:
    """在 ``root`` 内读取有界文件预览，并返回类型化候选上下文。"""

    root_path = Path(root)

    selected_files = rank_files(task, files, root=root_path)[:8]

    preview_budget = max(1200, max_chars // 3)
    file_previews = _read_file_previews(root_path, selected_files[:4], preview_budget)

    previous_task = str(working_memory.get("previous_task", "") or "")
    topic_relation = infer_topic_relation(task, previous_task)
    inherit_session = topic_relation in {"same_topic", "related_topic", "unknown"}

    working_memory_items = [str(item) for item in working_memory.recent()]
    working_memory_summary = working_memory.summary(
        max_chars=max(600, max_chars // 8)
    )
    long_term_memory = [
        item.render_prompt_line() for item in working_memory.long_term()
    ]
    if not inherit_session:
        working_memory_items = []
        working_memory_summary = (
            "Previous session context intentionally ignored because the topic changed."
        )

    retrieved_docs = retrieve(task, docs or files, limit=5)
    retrieved_docs = [truncate_middle(doc, 600) for doc in retrieved_docs]

    used = {
        "attention_sink": sum(len(item) for item in ATTENTION_SINK),
        "file_previews": sum(len(item) for item in file_previews),
        "retrieved_docs": sum(len(item) for item in retrieved_docs),
        "working_memory": len(working_memory_summary)
        + sum(len(item) for item in working_memory_items),
        "long_term_memory": sum(len(item) for item in long_term_memory),
    }
    dropped = []
    if used["file_previews"] >= preview_budget:
        dropped.append("some selected file content was middle-truncated")
    if not inherit_session:
        dropped.append("prior session memory was not inherited")

    return ContextStrategy(
        selected_files=selected_files,
        file_previews=file_previews,
        retrieved_docs=retrieved_docs,
        working_memory_items=working_memory_items,
        working_memory_summary=working_memory_summary,
        long_term_memory=long_term_memory,
        topic_relation=topic_relation,
        inherit_session=inherit_session,
        dropped_context=dropped,
        budget_breakdown=used,
    )


def infer_topic_relation(current_task: str, previous_task: str) -> str:

    if not previous_task:
        return "unknown"
    current_terms = set(_terms(current_task))
    previous_terms = set(_terms(previous_task))
    if not current_terms or not previous_terms:
        return "unknown"
    overlap = len(current_terms & previous_terms) / max(1, len(current_terms | previous_terms))
    if overlap >= 0.5:
        return "same_topic"
    if overlap >= 0.18:
        return "related_topic"
    return "topic_shift"


def _read_file_previews(root: Path, files: list[str], total_budget: int) -> list[str]:

    previews: list[str] = []
    if not files:
        return previews
    per_file_budget = max(400, total_budget // len(files))
    for rel_path in files:
        path = root / rel_path
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        preview = truncate_middle(text, per_file_budget)
        previews.append(f"### {rel_path}\n{preview}")
    return previews


def _terms(text: str) -> list[str]:

    normalized = text.lower()
    for mark in ["/", "_", "-", ".", ",", ":", ";", "(", ")", "[", "]"]:
        normalized = normalized.replace(mark, " ")
    return [part for part in normalized.split() if len(part) > 1]

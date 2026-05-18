from dataclasses import dataclass, field
from pathlib import Path

from .file_ranker import rank_files
from .rag import retrieve
from .token_budget import truncate_middle


ATTENTION_SINK = [
    "Follow the latest user task, not stale session history.",
    "Inspect relevant files before editing when the task depends on code.",
    "Return tool observations to the next reasoning step before deciding.",
    "Do not claim tests passed unless a validation tool actually succeeded.",
]


@dataclass
class ContextStrategy:
    """Policy result for one prompt-context assembly turn.

    A production coding agent cannot throw every file, every memory item, and
    every prior observation into the prompt. This object makes the context
    decision explicit: what we keep, what we compress, what we drop, and whether
    previous session state should influence the current user request.
    """

    selected_files: list[str]
    file_previews: list[str]
    retrieved_docs: list[str]
    memory_items: list[str]
    memory_summary: str
    topic_relation: str
    inherit_session: bool
    attention_sink: list[str] = field(default_factory=lambda: list(ATTENTION_SINK))
    dropped_context: list[str] = field(default_factory=list)
    budget_breakdown: dict[str, int] = field(default_factory=dict)


def build_context_strategy(
    task: str,
    files: list[str],
    docs: list[str],
    memory,
    root: str | Path,
    max_chars: int,
) -> ContextStrategy:
    """Select and compress context for one LLM call.

    The strategy is deliberately explainable rather than clever. It implements
    the senior-interview point that context engineering is a runtime policy:
    retrieve likely files, preserve an instruction anchor, compress memory, and
    avoid blindly inheriting unrelated prior turns.
    """

    root_path = Path(root)
    selected_files = rank_files(task, files, root=root_path)[:8]
    preview_budget = max(1200, max_chars // 3)
    file_previews = _read_file_previews(root_path, selected_files[:4], preview_budget)

    previous_task = ""
    if hasattr(memory, "get"):
        previous_task = str(memory.get("previous_task", "") or "")
    topic_relation = infer_topic_relation(task, previous_task)
    inherit_session = topic_relation in {"same_topic", "related_topic", "unknown"}

    memory_items = memory.recent() if hasattr(memory, "recent") else list(memory or [])
    memory_summary = memory.summary(max_chars=max(600, max_chars // 8)) if hasattr(memory, "summary") else ""
    if not inherit_session:
        memory_items = []
        memory_summary = "Previous session context intentionally ignored because the topic changed."

    retrieved_docs = retrieve(task, docs or files, limit=5)
    retrieved_docs = [truncate_middle(doc, 600) for doc in retrieved_docs]

    used = {
        "attention_sink": sum(len(item) for item in ATTENTION_SINK),
        "file_previews": sum(len(item) for item in file_previews),
        "retrieved_docs": sum(len(item) for item in retrieved_docs),
        "memory": len(memory_summary) + sum(len(item) for item in memory_items),
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
        memory_items=memory_items,
        memory_summary=memory_summary,
        topic_relation=topic_relation,
        inherit_session=inherit_session,
        dropped_context=dropped,
        budget_breakdown=used,
    )


def infer_topic_relation(current_task: str, previous_task: str) -> str:
    """Classify whether current task should inherit previous conversation state."""

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
    """Read bounded previews for selected files without letting one file dominate."""

    previews = []
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
    """Tokenize mixed Chinese/English task text for cheap topic heuristics."""

    normalized = text.lower()
    for mark in ["/", "_", "-", ".", ",", ":", ";", "(", ")", "[", "]"]:
        normalized = normalized.replace(mark, " ")
    return [part for part in normalized.split() if len(part) > 1]

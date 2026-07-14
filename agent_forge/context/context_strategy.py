from dataclasses import dataclass, field
from pathlib import Path

from .contracts import ContextMemory
from .file_ranker import rank_files
from .rag import retrieve
from .token_budget import truncate_middle


ATTENTION_SINK = [
    # These lines are deliberately stable and repeated in every context prompt.
    # In long multi-turn agents, the model may over-focus on recent tool output
    # and forget the task/policy. This "attention sink" is the prompt anchor
    # that keeps the latest user task, evidence-first editing, observation
    # feedback, and validation discipline visible on every turn.
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

    # Ranked file paths that the runtime thinks are most relevant to the task.
    # Project review angle: this is the "retrieval result" for coding context.
    selected_files: list[str]

    # Bounded code snippets for the highest-ranked files. The LLM needs some
    # real source text, not only file names, but previews must stay under budget.
    file_previews: list[str]

    # Lightweight lexical retrieval results. This project uses transparent
    # keyword retrieval rather than a vector DB because the repo is small and
    # code tasks need explainable evidence.
    retrieved_docs: list[str]

    # Raw recent memory items that survived topic-shift filtering.
    memory_items: list[str]

    # Compressed memory string. This is what prevents long conversations from
    # dumping every previous observation into the prompt.
    memory_summary: str

    # One of: unknown, same_topic, related_topic, topic_shift. It explains
    # whether previous session context should be trusted for this new task.
    topic_relation: str

    # True means prior session memory can enter the prompt; False means the
    # current request is treated as a fresh topic to avoid context pollution.
    inherit_session: bool

    # Stable instruction anchor described above.
    attention_sink: list[str] = field(default_factory=lambda: list(ATTENTION_SINK))

    # Human-readable explanation of what was intentionally not included.
    # This is useful in trace review when the agent missed something.
    dropped_context: list[str] = field(default_factory=list)

    # Character-level budget accounting by section. It is approximate, but it
    # lets you answer "how do you know where the context window is spent?"
    budget_breakdown: dict[str, int] = field(default_factory=dict)


def build_context_strategy(
    task: str,
    files: list[str],
    docs: list[str],
    memory: ContextMemory,
    root: str | Path,
    max_chars: int,
) -> ContextStrategy:
    """Select and compress context for one LLM call.

    The strategy is deliberately explainable rather than clever. It implements
    the senior technical walkthrough point that context engineering is a runtime policy:
    retrieve likely files, preserve an instruction anchor, compress memory, and
    avoid blindly inheriting unrelated prior turns.
    """

    root_path = Path(root)

    # 1. Select likely files first. This is the coding-agent equivalent of RAG
    # recall: use task terms, path names, and file content to choose candidates.
    selected_files = rank_files(task, files, root=root_path)[:8]

    # 2. Read only bounded previews for the top files. Previewing too many full
    # files is the classic way to waste context and dilute instruction following.
    preview_budget = max(1200, max_chars // 3)
    file_previews = _read_file_previews(root_path, selected_files[:4], preview_budget)

    # 3. Decide whether old session context is safe to reuse. This is deliberately
    # a cheap heuristic; the important design point is that memory inheritance is
    # explicit rather than automatic.
    previous_task = str(memory.get("previous_task", "") or "")
    topic_relation = infer_topic_relation(task, previous_task)
    inherit_session = topic_relation in {"same_topic", "related_topic", "unknown"}

    memory_items = [str(item) for item in memory.recent()]
    memory_summary = memory.summary(max_chars=max(600, max_chars // 8))
    if not inherit_session:
        memory_items = []
        memory_summary = "Previous session context intentionally ignored because the topic changed."

    # 4. Retrieve lightweight docs/path evidence. In a larger system this could
    # be BM25 + vector + reranker; here it stays deterministic for readability.
    retrieved_docs = retrieve(task, docs or files, limit=5)
    retrieved_docs = [truncate_middle(doc, 600) for doc in retrieved_docs]

    # 5. Record budget usage and dropped context so trace readers can debug
    # prompt quality without reverse-engineering the string rendering.
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
    """Classify whether current task should inherit previous conversation state.

    The thresholds are intentionally simple. The goal is not perfect intent
    recognition; it is to make the runtime conservative when a user jumps from
    one topic to another, such as "fix calculator" to "explain a technical walkthrough
    question". A real product could replace this with an intent classifier.
    """

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
    """Read bounded previews for selected files without letting one file dominate.

    Keeping source snippets in the prompt helps the model produce grounded tool
    calls. The per-file cap prevents a single large file from hiding tests,
    configs, or the actual target file.
    """

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
    """Tokenize mixed Chinese/English task text for cheap topic heuristics."""

    normalized = text.lower()
    for mark in ["/", "_", "-", ".", ",", ":", ";", "(", ")", "[", "]"]:
        normalized = normalized.replace(mark, " ")
    return [part for part in normalized.split() if len(part) > 1]

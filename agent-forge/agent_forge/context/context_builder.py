from dataclasses import dataclass

from .file_ranker import rank_files
from .rag import retrieve
from .token_budget import truncate


@dataclass
class ContextBuildReport:
    repo_map: str
    retrieved_docs: list[str]
    memory: list[str]
    selected_files: list[str]
    total_chars: int
    truncated: bool

    def render(self) -> str:
        docs = "\n".join(self.retrieved_docs)
        selected = "\n".join(self.selected_files)
        return (
            f"repo_map:\n{self.repo_map}\n"
            f"retrieved_docs:\n{docs}\n"
            f"memory:\n{self.memory}\n"
            f"selected_files:\n{selected}\n"
            f"total_chars:{self.total_chars}\n"
            f"truncated:{self.truncated}"
        )


def build_context_report(task, repo_map, memory, docs=None, max_chars=2000, root=".") -> ContextBuildReport:
    files = [line for line in repo_map.splitlines() if line.strip()]
    selected_files = rank_files(task, files, root=root)[:8]
    retrieved_docs = retrieve(task, docs or files)
    memory_items = memory.recent() if hasattr(memory, "recent") else list(memory or [])
    raw_repo = "\n".join(files)
    shortened = truncate(raw_repo, max_chars)
    total_chars = len(shortened) + sum(len(d) for d in retrieved_docs) + sum(len(m) for m in memory_items)
    return ContextBuildReport(
        repo_map=shortened,
        retrieved_docs=retrieved_docs,
        memory=memory_items,
        selected_files=selected_files,
        total_chars=total_chars,
        truncated=shortened != raw_repo,
    )


def build_context(task, repo_map, memory, tools):
    report = build_context_report(task, repo_map, memory)
    return f"task:{task}\n{report.render()}\ntools:{tools}"

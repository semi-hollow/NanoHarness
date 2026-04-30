from dataclasses import dataclass

from .file_ranker import rank_files
from .rag import retrieve
from .token_budget import truncate


@dataclass
class ContextBuildReport:
    system_prompt: str
    user_task: str
    repo_map: str
    retrieved_docs: list[str]
    memory: list[str]
    memory_summary: str
    selected_files: list[str]
    available_tools: list[str]
    permission_summary: str
    total_chars: int
    max_chars: int
    truncated: bool

    def render(self) -> str:
        docs = "\n".join(self.retrieved_docs)
        selected = "\n".join(self.selected_files)
        return (
            f"system:{self.system_prompt}\n"
            f"user_task:{self.user_task}\n"
            f"repo_map:\n{self.repo_map}\n"
            f"retrieved_docs:\n{docs}\n"
            f"memory:\n{self.memory}\n"
            f"memory_summary:{self.memory_summary}\n"
            f"selected_files:\n{selected}\n"
            f"available_tools:{self.available_tools}\n"
            f"permission_summary:{self.permission_summary}\n"
            f"total_chars:{self.total_chars}\n"
            f"max_chars:{self.max_chars}\n"
            f"truncated:{self.truncated}"
        )


def build_context_report(task, repo_map, memory, docs=None, max_chars=2000, root=".", tools=None, permission_summary="read allowed; write asks approval; dangerous commands denied") -> ContextBuildReport:
    files = [line for line in repo_map.splitlines() if line.strip()]
    selected_files = rank_files(task, files, root=root)[:8]
    retrieved_docs = retrieve(task, docs or files)
    memory_items = memory.recent() if hasattr(memory, "recent") else list(memory or [])
    memory_summary = memory.summary() if hasattr(memory, "summary") else "; ".join(str(x) for x in memory_items)
    raw_repo = "\n".join(files)
    shortened = truncate(raw_repo, max_chars)
    available_tools = [t.get("name", str(t)) for t in (tools or [])]
    system_prompt = "You are Agent Forge, a controlled coding-agent harness. Use tools safely and report unverified work."
    total_chars = (
        len(system_prompt)
        + len(task)
        + len(shortened)
        + sum(len(d) for d in retrieved_docs)
        + len(memory_summary)
        + sum(len(t) for t in available_tools)
        + len(permission_summary)
    )
    return ContextBuildReport(
        system_prompt=system_prompt,
        user_task=task,
        repo_map=shortened,
        retrieved_docs=retrieved_docs,
        memory=memory_items,
        memory_summary=memory_summary,
        selected_files=selected_files,
        available_tools=available_tools,
        permission_summary=permission_summary,
        total_chars=total_chars,
        max_chars=max_chars,
        truncated=shortened != raw_repo,
    )


def build_context(task, repo_map, memory, tools):
    report = build_context_report(task, repo_map, memory, tools=tools)
    return f"task:{task}\n{report.render()}\ntools:{tools}"

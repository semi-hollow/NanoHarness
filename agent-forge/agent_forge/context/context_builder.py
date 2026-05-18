from dataclasses import dataclass

from .token_budget import truncate
from .context_strategy import build_context_strategy


@dataclass
class ContextBuildReport:
    """All context pieces assembled for one LLM turn."""

    system_prompt: str
    user_task: str
    repo_map: str
    retrieved_docs: list[str]
    memory: list[str]
    memory_summary: str
    selected_files: list[str]
    selected_file_previews: list[str]
    available_tools: list[str]
    permission_summary: str
    attention_sink: list[str]
    topic_relation: str
    inherit_session: bool
    dropped_context: list[str]
    budget_breakdown: dict[str, int]
    total_chars: int
    max_chars: int
    truncated: bool

    def render(self) -> str:
        """Serialize context into the system message consumed by LLM clients.

        The format is boring on purpose. It gives the model stable sections and
        gives a human reader a direct mapping from prompt text back to runtime
        decisions: attention anchor, selected files, memory policy, tools, and
        permission boundaries.
        """

        docs = "\n".join(self.retrieved_docs)
        selected = "\n".join(self.selected_files)
        previews = "\n\n".join(self.selected_file_previews)
        sink = "\n".join(f"- {item}" for item in self.attention_sink)
        return (
            f"system:{self.system_prompt}\n"
            f"attention_sink:\n{sink}\n"
            f"user_task:{self.user_task}\n"
            f"repo_map:\n{self.repo_map}\n"
            f"selected_file_previews:\n{previews}\n"
            f"retrieved_docs:\n{docs}\n"
            f"memory:\n{self.memory}\n"
            f"memory_summary:{self.memory_summary}\n"
            f"topic_relation:{self.topic_relation}\n"
            f"inherit_session:{self.inherit_session}\n"
            f"dropped_context:{self.dropped_context}\n"
            f"selected_files:\n{selected}\n"
            f"available_tools:{self.available_tools}\n"
            f"permission_summary:{self.permission_summary}\n"
            f"budget_breakdown:{self.budget_breakdown}\n"
            f"total_chars:{self.total_chars}\n"
            f"max_chars:{self.max_chars}\n"
            f"truncated:{self.truncated}"
        )


def build_context_report(
    task,
    repo_map,
    memory,
    docs=None,
    max_chars=8000,
    root=".",
    tools=None,
    permission_summary="read allowed; write asks approval; dangerous commands denied",
) -> ContextBuildReport:
    """Build the context object AgentLoop sends to the next LLM call."""

    files = [line for line in repo_map.splitlines() if line.strip()]
    raw_repo = "\n".join(files)
    shortened = truncate(raw_repo, max(1000, max_chars // 4))
    available_tools = [t.get("name", str(t)) for t in (tools or [])]
    strategy = build_context_strategy(
        task=task,
        files=files,
        docs=docs or files,
        memory=memory,
        root=root,
        max_chars=max_chars,
    )
    system_prompt = (
        "You are Agent Forge, a controlled coding-agent runtime. "
        "Use ReAct-style reasoning through tools, prefer evidence over guesses, "
        "recover from failed observations when retryable, and report unverified work."
    )
    total_chars = (
        len(system_prompt)
        + len(task)
        + len(shortened)
        + sum(len(d) for d in strategy.retrieved_docs)
        + sum(len(d) for d in strategy.file_previews)
        + len(strategy.memory_summary)
        + sum(len(t) for t in available_tools)
        + len(permission_summary)
        + sum(len(t) for t in strategy.attention_sink)
    )
    return ContextBuildReport(
        system_prompt=system_prompt,
        user_task=task,
        repo_map=shortened,
        retrieved_docs=strategy.retrieved_docs,
        memory=strategy.memory_items,
        memory_summary=strategy.memory_summary,
        selected_files=strategy.selected_files,
        selected_file_previews=strategy.file_previews,
        available_tools=available_tools,
        permission_summary=permission_summary,
        attention_sink=strategy.attention_sink,
        topic_relation=strategy.topic_relation,
        inherit_session=strategy.inherit_session,
        dropped_context=strategy.dropped_context,
        budget_breakdown=strategy.budget_breakdown,
        total_chars=total_chars,
        max_chars=max_chars,
        truncated=shortened != raw_repo,
    )


def build_context(task, repo_map, memory, tools):
    """Backward-compatible helper returning rendered context as a string."""

    report = build_context_report(task, repo_map, memory, tools=tools)
    return f"task:{task}\n{report.render()}\ntools:{tools}"

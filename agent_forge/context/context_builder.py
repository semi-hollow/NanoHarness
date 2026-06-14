from dataclasses import dataclass
from pathlib import Path

from agent_forge.runtime.prompt_registry import PromptRegistry

from .token_budget import truncate
from .context_strategy import build_context_strategy


@dataclass
class ContextBuildReport:
    """All context pieces assembled for one LLM turn.

    This dataclass is intentionally verbose. In an agent technical walkthrough, context
    quality is one of the hardest parts to explain, so each field maps to a
    trace-visible section that answers "why did the model see this?"
    """

    # Stable system instruction. This is separate from the user task so it can
    # carry runtime policy: evidence-first, use tools, recover safely.
    system_prompt: str

    # Project-level runtime instructions loaded from FORGE.md. These are
    # separate from model prompt text because they are repository policy, not
    # generic agent behavior.
    project_instructions: str

    # Latest user task. It is kept explicit to avoid the model over-weighting
    # old memory in multi-turn runs.
    user_task: str

    # Bounded repository file map. It helps the model understand project shape
    # without reading the full repo.
    repo_map: str

    # Lightweight lexical retrieval results, usually file paths or short docs.
    retrieved_docs: list[str]

    # Recent short-term memory items selected by Memory/ContextStrategy.
    memory: list[str]

    # Compressed memory summary for older observations/session seed.
    memory_summary: str

    # Ranked candidate files for this task. These are trace evidence.
    selected_files: list[str]

    # Bounded code snippets from the highest-ranked files.
    selected_file_previews: list[str]

    # Tool names exposed to the model this turn. In multi-agent, role allowlists
    # can shrink this list.
    available_tools: list[str]

    # Human-readable permission boundary shown to the model.
    permission_summary: str

    # Stable instruction anchor that survives long contexts.
    attention_sink: list[str]

    # Topic continuity classification for session memory inheritance.
    topic_relation: str

    # Whether previous session memory is allowed into this turn.
    inherit_session: bool

    # Explanation of dropped/compressed context for trace debugging.
    dropped_context: list[str]

    # Approximate character budget by section.
    budget_breakdown: dict[str, int]

    # Total approximate prompt chars after assembly.
    total_chars: int

    # Configured max context chars used by the strategy.
    max_chars: int

    # True if the repo map had to be truncated.
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
            f"project_instructions:\n{self.project_instructions}\n"
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
    """Build the context object AgentLoop sends to the next LLM call.

    The function is intentionally split from AgentLoop so prompt construction is
    an auditable policy layer. If the agent behaves badly, you can inspect the
    trace and ask whether retrieval, memory inheritance, or token budget was the
    cause.
    """

    files = [line for line in repo_map.splitlines() if line.strip()]
    raw_repo = "\n".join(files)

    # Repo map gets only a fraction of the budget. A file tree is useful, but
    # source previews and recent observations usually matter more for coding.
    shortened = truncate(raw_repo, max(1000, max_chars // 4))
    available_tools = [t.get("name", str(t)) for t in (tools or [])]

    # ContextStrategy owns selection/compression. This wrapper owns rendering
    # and trace-friendly accounting.
    strategy = build_context_strategy(
        task=task,
        files=files,
        docs=docs or files,
        memory=memory,
        root=root,
        max_chars=max_chars,
    )
    prompt = PromptRegistry().get("agent_system")
    project_instructions = load_project_instructions(root)
    system_prompt = f"[prompt:{prompt.header()} purpose:{prompt.purpose}]\n{prompt.content}"
    total_chars = (
        len(system_prompt)
        + len(project_instructions)
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
        project_instructions=project_instructions,
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


def load_project_instructions(root: str | Path, max_chars: int = 2600) -> str:
    """Load FORGE.md as repository-specific instructions.

    This is the local equivalent of project instruction files used by coding
    agents. Keeping it in the prompt context makes repo rules auditable: if the
    agent violates a rule, trace readers can confirm whether the rule was
    present in context.
    """

    path = Path(root) / "FORGE.md"
    if not path.exists():
        return "FORGE.md not found; follow built-in runtime policy."
    text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 14] + " [truncated]"

from dataclasses import dataclass
from pathlib import Path

from agent_forge.contracts import ToolSchema
from agent_forge.runtime.prompt_registry import PromptRegistry

from .contracts import ContextMemory
from .token_budget import truncate
from .context_strategy import build_context_strategy


@dataclass
class ContextBuildReport:
    """一次上下文组装的完整读模型，可渲染给模型并写入 trace。"""

    system_prompt: str
    project_instructions: str
    user_task: str
    repo_map: str
    retrieved_docs: list[str]
    memory: list[str]
    memory_summary: str
    selected_files: list[str]
    selected_file_previews: list[str]
    available_tools: list[str]
    active_skill_cards: list[str]
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
        """按稳定区段顺序渲染模型输入上下文。"""

        docs = "\n".join(self.retrieved_docs)
        selected = "\n".join(self.selected_files)
        previews = "\n\n".join(self.selected_file_previews)
        skills = "\n\n".join(self.active_skill_cards)
        sink = "\n".join(f"- {item}" for item in self.attention_sink)
        return (
            f"system:{self.system_prompt}\n"
            f"project_instructions:\n{self.project_instructions}\n"
            f"attention_sink:\n{sink}\n"
            f"active_skills:\n{skills}\n"
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

# 主要入口：下方定义承接该模块的核心调用。
def build_context_report(
    task: str,
    repo_map: str,
    memory: ContextMemory,
    docs: list[str] | None = None,
    max_chars: int = 8000,
    root: str | Path = ".",
    tools: list[ToolSchema] | None = None,
    active_skill_cards: list[str] | None = None,
    permission_summary: str = "read allowed; write asks approval; dangerous commands denied",
) -> ContextBuildReport:
    """汇总项目指令、仓库结构、检索结果、记忆、Skill 和工具契约。"""

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
        + sum(len(card) for card in (active_skill_cards or []))
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
        active_skill_cards=active_skill_cards or [],
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


def build_context(
    task: str,
    repo_map: str,
    memory: ContextMemory,
    tools: list[ToolSchema],
) -> str:

    report = build_context_report(task, repo_map, memory, tools=tools)
    return f"task:{task}\n{report.render()}\ntools:{tools}"


def load_project_instructions(root: str | Path, max_chars: int = 2600) -> str:

    path = Path(root) / "FORGE.md"
    if not path.exists():
        return "FORGE.md not found; follow built-in runtime policy."
    text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 14] + " [truncated]"

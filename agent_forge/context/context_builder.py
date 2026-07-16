from dataclasses import dataclass
from pathlib import Path

from agent_forge.contracts import ToolSchema
from agent_forge.runtime.prompt_registry import PromptRegistry

from .contracts import ContextMemory
from .token_budget import truncate, truncate_middle
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
    long_term_memory: list[str]
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
    rendered_context: str = ""

    def render(self) -> str:
        """按稳定区段顺序渲染模型输入上下文。"""

        return self.rendered_context or _fit_context_sections(self)[0]

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
    if shortened != raw_repo:
        strategy.dropped_context.append(
            "repository map pre-truncated before section allocation"
        )
    prompt = PromptRegistry().get("agent_system")
    project_instructions = load_project_instructions(root)
    system_prompt = f"[prompt:{prompt.header()} purpose:{prompt.purpose}]\n{prompt.content}"
    report = ContextBuildReport(
        system_prompt=system_prompt,
        project_instructions=project_instructions,
        user_task=task,
        repo_map=shortened,
        retrieved_docs=strategy.retrieved_docs,
        memory=strategy.memory_items,
        memory_summary=strategy.memory_summary,
        long_term_memory=strategy.long_term_memory,
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
        total_chars=0,
        max_chars=max(512, int(max_chars)),
        truncated=False,
    )
    rendered, included, truncated_sections = _fit_context_sections(report)
    report.rendered_context = rendered
    report.total_chars = len(rendered)
    report.budget_breakdown = included
    report.truncated = bool(truncated_sections) or any(
        "truncat" in item for item in report.dropped_context
    )
    report.dropped_context.extend(
        f"{name} truncated to context budget" for name in truncated_sections
    )
    return report


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


def _fit_context_sections(
    report: ContextBuildReport,
) -> tuple[str, dict[str, int], list[str]]:
    """按稳定权重压缩区段，保证静态 system message 不超过显式预算。"""

    sections = [
        ("system", report.system_prompt, 14),
        ("permission_summary", report.permission_summary, 8),
        (
            "attention_sink",
            "\n".join(f"- {item}" for item in report.attention_sink),
            8,
        ),
        ("available_tools", ", ".join(report.available_tools), 5),
        ("active_skills", "\n\n".join(report.active_skill_cards), 8),
        (
            "long_term_memory",
            "\n".join(f"- {item}" for item in report.long_term_memory),
            11,
        ),
        ("project_instructions", report.project_instructions, 10),
        (
            "selected_file_previews",
            "\n\n".join(report.selected_file_previews),
            22,
        ),
        ("retrieved_docs", "\n".join(report.retrieved_docs), 5),
        ("memory_summary", report.memory_summary, 4),
        ("working_memory", "\n".join(str(item) for item in report.memory), 2),
        ("repo_map", report.repo_map, 6),
        ("selected_files", "\n".join(report.selected_files), 3),
        (
            "context_state",
            (
                f"topic_relation={report.topic_relation}; "
                f"inherit_session={report.inherit_session}; "
                f"dropped={report.dropped_context}"
            ),
            2,
        ),
    ]
    active = [section for section in sections if section[1]]
    label_chars = sum(len(name) + 3 for name, _, _ in active)
    content_budget = max(0, report.max_chars - label_chars)
    budgets = _weighted_budgets(active, content_budget)
    blocks: list[str] = []
    included: dict[str, int] = {}
    truncated_sections: list[str] = []
    for (name, content, _), budget in zip(active, budgets):
        value = truncate_middle(content, budget)
        included[name] = len(value)
        if len(value) < len(content):
            truncated_sections.append(name)
        blocks.append(f"{name}:\n{value}\n")
    return "".join(blocks), included, truncated_sections


def _weighted_budgets(
    sections: list[tuple[str, str, int]],
    total_budget: int,
) -> list[int]:
    """先按权重分配，再把短区段未使用的额度返还给仍有内容的区段。"""

    if total_budget <= 0:
        return [0 for _ in sections]
    weight_total = sum(weight for _, _, weight in sections)
    budgets = [
        min(len(content), total_budget * weight // weight_total)
        for _, content, weight in sections
    ]
    remaining = total_budget - sum(budgets)
    while remaining > 0:
        candidates = [
            index
            for index, (_, content, _) in enumerate(sections)
            if budgets[index] < len(content)
        ]
        if not candidates:
            break
        candidate_weight = sum(sections[index][2] for index in candidates)
        granted = 0
        for index in candidates:
            available = len(sections[index][1]) - budgets[index]
            share = max(
                1,
                remaining * sections[index][2] // candidate_weight,
            )
            amount = min(available, share, remaining - granted)
            budgets[index] += amount
            granted += amount
            if granted >= remaining:
                break
        if granted <= 0:
            break
        remaining -= granted
    return budgets

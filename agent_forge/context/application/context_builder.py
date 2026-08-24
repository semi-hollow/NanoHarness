"""Turn 稳定前缀与 Model Step 动态仓库上下文的独立预算组装。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_forge.context.application.system_prompt import PromptRegistry
from agent_forge.context.ports import ContextMemory
from agent_forge.contracts import JsonObject, ToolSchema

from .context_strategy import ContextStrategyRequest, build_context_strategy
from .instructions import InstructionResolutionRequest, resolve_instructions
from .text_budget import truncate, truncate_middle


@dataclass(frozen=True)
class TurnSystemContextBuildPolicy:
    """一个上下文分区的字符预算与当前动态权限摘要。"""

    max_chars: int = 8_000
    permission_summary: str = ""


@dataclass(frozen=True)
class StableTurnContextBuildRequest:
    """新 Turn 首次 attempt 冻结一次的 Prompt/Skill/Memory 输入。"""

    root_task: str
    root: str | Path
    base_tool_schemas: list[ToolSchema]
    active_skill_cards: list[str]
    long_term_memory: list[str]
    policy: TurnSystemContextBuildPolicy
    instruction_target: str = ""
    global_instruction_files: tuple[str, ...] = ()
    runtime_instructions: str = ""
    instruction_max_bytes: int = 2_600
    system_prompt_profile: str = "single_agent"


@dataclass(frozen=True)
class StableTurnContextBuildReport:
    """可持久化进 ``TurnContextSnapshot`` 的稳定前缀与构建证据。"""

    rendered_prefix: str
    total_chars: int
    max_chars: int
    truncated: bool
    dropped_context: list[str]
    budget_breakdown: dict[str, int]
    instruction_evidence: JsonObject
    available_tools: list[str]

    def render(self) -> str:
        return self.rendered_prefix


@dataclass(frozen=True)
class TurnSystemContextBuildRequest:
    """每个 Model Step 只重建的任务焦点、仓库和派生状态候选。"""

    turn_focus: str
    stable_system_prefix: str
    repo_map: str
    working_memory: ContextMemory
    root: str | Path
    tool_schemas: list[ToolSchema]
    policy: TurnSystemContextBuildPolicy
    frozen_instruction_paths: tuple[str, ...] = ()


@dataclass
class TurnSystemContextBuildReport:
    """稳定前缀与动态候选合并后的当前模型输入读模型。"""

    stable_system_prefix: str
    repo_map: str
    retrieved_docs: list[str]
    working_memory_items: list[str]
    working_memory_summary: str
    selected_files: list[str]
    selected_file_previews: list[str]
    available_tools: list[str]
    permission_summary: str
    attention_sink: list[str]
    dropped_context: list[str]
    budget_breakdown: dict[str, int]
    total_chars: int
    max_chars: int
    truncated: bool
    stable_chars: int
    dynamic_chars: int
    dynamic_max_chars: int
    rendered_context: str = ""

    def render(self) -> str:
        return self.rendered_context or _render_model_step_context(self)[0]


def build_stable_turn_context(
    request: StableTurnContextBuildRequest,
) -> StableTurnContextBuildReport:
    """冻结新 Turn 的稳定前缀；同 Turn 后续 Run 只加载，不重新发现。"""

    # region 1. 一次性发现：角色 Prompt、分层指令、Skill、Memory 和基础工具契约
    prompt = PromptRegistry().get(request.system_prompt_profile)
    instruction_resolution = resolve_instructions(
        InstructionResolutionRequest(
            workspace=request.root,
            active_path=request.instruction_target,
            global_files=request.global_instruction_files,
            runtime_override=request.runtime_instructions,
            max_bytes=request.instruction_max_bytes,
        )
    )
    base_tool_names = [
        str(schema.get("name") or "")
        for schema in request.base_tool_schemas
        if str(schema.get("name") or "")
    ]
    system_content = f"[prompt:{prompt.header()} purpose:{prompt.purpose}]\n{prompt.content}"
    sections = [
        ("project_instructions", instruction_resolution.content, 18),
        ("active_skills", "\n\n".join(request.active_skill_cards), 13),
        (
            "long_term_memory",
            "\n".join(f"- {item}" for item in request.long_term_memory),
            13,
        ),
        ("base_tools", ", ".join(base_tool_names), 4),
    ]
    # endregion 1. 一次性发现结束

    # region 2. 独立预算：稳定前缀不能被动态仓库内容挤占
    max_chars = max(256, int(request.policy.max_chars))
    mandatory_system_block = f"system:\n{system_content}\n"
    if len(mandatory_system_block) > max_chars:
        raise ValueError(
            "stable context budget cannot contain the complete governing System Prompt"
        )
    remaining_budget = max_chars - len(mandatory_system_block)
    optional_rendered, included, truncated_sections = _fit_sections(
        sections,
        remaining_budget,
    )
    rendered = mandatory_system_block + optional_rendered
    included = {"system": len(system_content), **included}
    dropped = [f"{name} truncated to stable-prefix budget" for name in truncated_sections]
    return StableTurnContextBuildReport(
        rendered_prefix=rendered,
        total_chars=len(rendered),
        max_chars=max_chars,
        truncated=bool(truncated_sections),
        dropped_context=dropped,
        budget_breakdown=included,
        instruction_evidence=instruction_resolution.to_evidence(),
        available_tools=base_tool_names,
    )
    # endregion 2. 独立预算结束


def build_turn_system_context(
    request: TurnSystemContextBuildRequest,
) -> TurnSystemContextBuildReport:
    """按最新 ``turn_focus`` 构造动态仓库上下文，再附到冻结前缀之后。"""

    # region 1. 动态候选：结构图、文件预览、检索和派生 WorkingMemory
    files = [line for line in request.repo_map.splitlines() if line.strip()]
    raw_repo = "\n".join(files)
    dynamic_max_chars = max(256, int(request.policy.max_chars))
    shortened_repo = truncate(raw_repo, max(256, dynamic_max_chars // 4))
    strategy = build_context_strategy(
        ContextStrategyRequest(
            turn_focus=request.turn_focus,
            files=files,
            working_memory=request.working_memory,
            root=request.root,
            max_chars=dynamic_max_chars,
            frozen_instruction_paths=request.frozen_instruction_paths,
        )
    )
    if shortened_repo != raw_repo:
        strategy.dropped_context.append(
            "repository map pre-truncated before dynamic section allocation"
        )
    # endregion 1. 动态候选结束

    report = TurnSystemContextBuildReport(
        stable_system_prefix=request.stable_system_prefix,
        repo_map=shortened_repo,
        retrieved_docs=strategy.retrieved_docs,
        working_memory_items=strategy.working_memory_items,
        working_memory_summary=strategy.working_memory_summary,
        selected_files=strategy.selected_files,
        selected_file_previews=strategy.file_previews,
        available_tools=[
            str(schema.get("name") or "")
            for schema in request.tool_schemas
            if str(schema.get("name") or "")
        ],
        permission_summary=request.policy.permission_summary,
        attention_sink=strategy.attention_sink,
        dropped_context=strategy.dropped_context,
        budget_breakdown={},
        total_chars=0,
        max_chars=len(request.stable_system_prefix) + dynamic_max_chars,
        truncated=False,
        stable_chars=len(request.stable_system_prefix),
        dynamic_chars=0,
        dynamic_max_chars=dynamic_max_chars,
    )
    rendered, included, truncated_sections = _render_model_step_context(report)
    report.rendered_context = rendered
    report.total_chars = len(rendered)
    report.dynamic_chars = sum(included.values())
    report.budget_breakdown = {"stable_prefix": report.stable_chars, **included}
    report.truncated = bool(truncated_sections) or any(
        "truncat" in item for item in report.dropped_context
    )
    report.dropped_context.extend(
        f"{name} truncated to dynamic-context budget" for name in truncated_sections
    )
    return report


def load_project_instructions(root: str | Path, max_chars: int = 2_600) -> str:
    """保留给局部调用方的根目录解析入口；主链使用稳定快照。"""

    return resolve_instructions(
        InstructionResolutionRequest(workspace=root, max_bytes=max_chars)
    ).content


def _render_model_step_context(
    report: TurnSystemContextBuildReport,
) -> tuple[str, dict[str, int], list[str]]:
    sections = [
        ("permission_summary", report.permission_summary, 10),
        (
            "attention_sink",
            "\n".join(f"- {item}" for item in report.attention_sink),
            8,
        ),
        ("available_tools", ", ".join(report.available_tools), 6),
        ("selected_file_previews", "\n\n".join(report.selected_file_previews), 28),
        ("retrieved_docs", "\n".join(report.retrieved_docs), 8),
        ("working_memory_summary", report.working_memory_summary, 7),
        (
            "working_memory_items",
            "\n".join(str(item) for item in report.working_memory_items),
            5,
        ),
        ("repo_map", report.repo_map, 8),
        ("selected_files", "\n".join(report.selected_files), 4),
    ]
    dynamic, included, truncated = _fit_sections(sections, report.dynamic_max_chars)
    prefix = report.stable_system_prefix.rstrip()
    return f"{prefix}\n\n{dynamic}" if prefix else dynamic, included, truncated


def _fit_sections(
    sections: list[tuple[str, str, int]],
    max_chars: int,
) -> tuple[str, dict[str, int], list[str]]:
    """按稳定权重分配一个分区预算，并返回真实使用量。"""

    candidates = [section for section in sections if section[1]]
    active: list[tuple[str, str, int]] = []
    skipped_labels: list[str] = []
    remaining_label_budget = max_chars
    for section in candidates:
        label_cost = len(section[0]) + 3
        if label_cost <= remaining_label_budget:
            active.append(section)
            remaining_label_budget -= label_cost
        else:
            skipped_labels.append(section[0])
    label_chars = sum(len(name) + 3 for name, _, _ in active)
    content_budget = max(0, max_chars - label_chars)
    budgets = _weighted_budgets(active, content_budget)
    blocks: list[str] = []
    included: dict[str, int] = {}
    truncated_sections: list[str] = list(skipped_labels)
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
            share = max(1, remaining * sections[index][2] // candidate_weight)
            amount = min(available, share, remaining - granted)
            budgets[index] += amount
            granted += amount
            if granted >= remaining:
                break
        if granted <= 0:
            break
        remaining -= granted
    return budgets

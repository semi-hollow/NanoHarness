"""Repository context 的选择与只读预览策略；不调用模型、不写外部状态。

系统角色：根据当前 ``turn_focus`` 从 Repo Map、文件预览、检索候选和派生
WorkingMemory 中选择一个有界动态上下文视图。
输入：``ContextStrategyRequest``；输出：``ContextStrategy`` 与 dropped/budget 证据。
相邻边界：稳定 Instructions/LTM 不在这里重读；Context Builder 负责最终 section 组装。

核心阅读：``build_context_strategy`` 的三个折叠阶段。
"""

from dataclasses import dataclass, field
from pathlib import Path

from agent_forge.context.ports import ContextMemory
from .file_ranker import rank_files
from .retrieval import retrieve
from .text_budget import truncate_middle

ATTENTION_SINK = [
    "Follow the current Turn focus while preserving its root task constraints.",
    "Inspect relevant files before editing when the task depends on code.",
    "Return tool observations to the next reasoning step before deciding.",
    "Do not claim tests passed unless a validation tool actually succeeded.",
]


# 核心数据：Context 策略选择文件与记忆时使用的候选输入。
@dataclass(frozen=True)
class ContextStrategyRequest:
    """任务、仓库文件、工作记忆和本次字符预算。"""

    turn_focus: str
    files: list[str]
    working_memory: ContextMemory
    root: str | Path
    max_chars: int
    frozen_instruction_paths: tuple[str, ...] = ()


# 核心数据：文件、检索、working/long-term memory 的一次选择结果。
@dataclass
class ContextStrategy:
    """静态 system context 进入预算分配前的类型化候选集合。

    文件字段记录 ranking 与 preview；memory 字段明确区分 working items、working
    summary；长期记忆属于冻结的 Turn snapshot，不在动态策略重复召回。attention、
    dropped 和 budget 字段提供治理规则与可观测证据。
    """

    selected_files: list[str]
    file_previews: list[str]
    retrieved_docs: list[str]
    working_memory_items: list[str]
    working_memory_summary: str
    attention_sink: list[str] = field(default_factory=lambda: list(ATTENTION_SINK))
    dropped_context: list[str] = field(default_factory=list)
    budget_breakdown: dict[str, int] = field(default_factory=dict)


# 核心规则：按任务相关性选择文件、检索结果和可继承的记忆视图。
def build_context_strategy(request: ContextStrategyRequest) -> ContextStrategy:
    """在 ``root`` 内读取有界文件预览，并返回类型化候选上下文。

    伪代码：按最新 ``turn_focus`` 选择文件与预览 -> 读取派生 WorkingMemory
    -> 生成 retrieval 与预算/丢弃证据。
    """

    root_path = Path(request.root).resolve()
    frozen_instruction_paths = _workspace_relative_paths(
        root_path,
        request.frozen_instruction_paths,
    )

    # region 1. 仓库候选：冻结规则文件不能作为动态正文再次进入同一 Turn
    # 指令文件的旧正文已经属于 StableTurnContextSnapshot。即使 Tool 在本 Turn 中改写它，
    # 动态 preview/retrieval 也只从其余文件选取，避免新正文绕过 snapshot 改变治理规则。
    dynamic_files = [
        path for path in request.files if path not in frozen_instruction_paths
    ]
    selected_files = rank_files(request.turn_focus, dynamic_files, root=root_path)[:8]

    preview_budget = max(1200, request.max_chars // 3)
    file_previews = _read_file_previews(root_path, selected_files[:4], preview_budget)
    # endregion 1. 仓库候选结束

    # region 2. WorkingMemory：只读取本 Turn 已提炼的任务状态
    working_memory_items = [str(item) for item in request.working_memory.recent()]
    working_memory_summary = request.working_memory.summary(
        max_chars=max(600, request.max_chars // 8)
    )
    # endregion 2. WorkingMemory 结束

    # region 3. 检索与证据：生成其余候选，并记录实际预算和丢弃原因
    retrieved_docs = retrieve(request.turn_focus, dynamic_files, limit=5)
    retrieved_docs = [truncate_middle(doc, 600) for doc in retrieved_docs]

    used = {
        "attention_sink": sum(len(item) for item in ATTENTION_SINK),
        "file_previews": sum(len(item) for item in file_previews),
        "retrieved_docs": sum(len(item) for item in retrieved_docs),
        "working_memory": len(working_memory_summary)
        + sum(len(item) for item in working_memory_items),
    }
    dropped = []
    if used["file_previews"] >= preview_budget:
        dropped.append("some selected file content was middle-truncated")
    return ContextStrategy(
        selected_files=selected_files,
        file_previews=file_previews,
        retrieved_docs=retrieved_docs,
        working_memory_items=working_memory_items,
        working_memory_summary=working_memory_summary,
        dropped_context=dropped,
        budget_breakdown=used,
    )
    # endregion 3. 检索与证据结束


def _workspace_relative_paths(root: Path, raw_paths: tuple[str, ...]) -> set[str]:
    """把 snapshot 中的绝对 instruction source 归一化为 Repo Map 路径。"""

    result: set[str] = set()
    for raw_path in raw_paths:
        if not raw_path or raw_path.startswith("<"):
            continue
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            result.add(candidate.resolve().relative_to(root).as_posix())
        except (OSError, ValueError):
            # workspace 外的 global instruction 不可能出现在本仓库 Repo Map 中。
            continue
    return result


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

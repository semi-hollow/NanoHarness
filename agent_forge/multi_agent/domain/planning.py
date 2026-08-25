"""Planner model output 到 canonical ``FanoutPlan`` 之间的 Domain contract。

本文件只解析和校验模型提议，不调用模型、不调度 Worker。折叠后只保留三块：
1 单个任务；2 Single/Multi decision；3 无状态输入 helper。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .fanout import FanoutPlan, SubagentTask
from .live_handoff import LiveDependency

MAX_CRITERIA = 16
MAX_CRITERION_CHARS = 500


# region 1. 单个 Planner task：仍是提议，尚未获得 Runtime 执行权
@dataclass(frozen=True)
class PlannedTask:
    """Planner 可以提出、但必须由 Runtime 校验的一个任务。"""

    id: str
    task: str
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    write_scope: tuple[str, ...] = field(default_factory=tuple)
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    acceptance_criteria: tuple[str, ...] = field(default_factory=tuple)
    max_steps: int = 12

    def __post_init__(self) -> None:
        object.__setattr__(self, "depends_on", tuple(self.depends_on))
        object.__setattr__(self, "write_scope", tuple(self.write_scope))
        object.__setattr__(self, "allowed_tools", tuple(self.allowed_tools))
        object.__setattr__(
            self, "acceptance_criteria", tuple(self.acceptance_criteria)
        )

    def to_mapping(self) -> dict[str, Any]:
        """投影成 ``FanoutPlan.from_mapping`` 可继续校验的结构。"""

        return {
            "id": self.id,
            "task": self.task,
            "depends_on": list(self.depends_on),
            "write_scope": list(self.write_scope),
            "allowed_tools": list(self.allowed_tools),
            "acceptance_criteria": list(self.acceptance_criteria),
            "max_steps": self.max_steps,
        }
# endregion 1. 单个 Planner task 结束


# region 2. PlanningDecision：解析策略门，并把 multi 提议送入唯一 FanoutPlan
@dataclass(frozen=True)
class PlanningDecision:
    """Single/Multi 策略和 Multi-Agent 候选任务的已解析结果。"""

    mode: str
    reason: str
    global_acceptance_criteria: tuple[str, ...] = field(default_factory=tuple)
    tasks: tuple[PlannedTask, ...] = field(default_factory=tuple)
    live_dependencies: tuple[LiveDependency, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "global_acceptance_criteria",
            tuple(self.global_acceptance_criteria),
        )
        object.__setattr__(self, "tasks", tuple(self.tasks))
        object.__setattr__(self, "live_dependencies", tuple(self.live_dependencies))

    @classmethod
    def from_mapping(
        cls,
        data: dict[str, Any],
        *,
        available_tools: Iterable[str],
        max_fanout_tasks: int = 16,
    ) -> "PlanningDecision":
        """解析结构化模型输出，并拒绝模式、工具和预算越界。

        伪代码：校验 Single/Multi header -> 校验 tasks/live 容器与模式一致
        -> 逐 Task 限制工具和预算 -> 构造 typed decision，等待 FanoutPlan 图校验。
        """

        # region 1. Decision header：先确定 Single/Multi 和全局验收边界
        # Parser 正常会提供 object；Domain 仍独立保护直接调用路径。
        if not isinstance(data, dict):
            raise ValueError("planning decision must be an object")
        mode = str(data.get("mode") or "").strip().lower()
        # 策略门只允许对外公开的 Single/Multi 两种决策。
        if mode not in {"single", "multi"}:
            raise ValueError("planning mode must be single or multi")
        reason = str(data.get("reason") or "").strip()
        # 决策必须能解释为何选择该执行模式，空 reason 不可审计。
        if not reason:
            raise ValueError("planning reason must not be empty")
        global_criteria = _criteria(
            data.get("global_acceptance_criteria"),
            "global_acceptance_criteria",
        )
        rows = data.get("tasks", [])
        # tasks 必须是显式数组，即使 Single 模式也使用空数组而非其他形状。
        if not isinstance(rows, list):
            raise ValueError("planning tasks must be a list")
        # Single 不能偷偷携带将被忽略的 Multi-Agent tasks。
        if mode == "single" and rows:
            raise ValueError("single planning decision must not contain multi-agent tasks")
        live_rows = data.get("live_dependencies", [])
        # LIVE edges 同样必须为显式数组。
        if not isinstance(live_rows, list):
            raise ValueError("planning live_dependencies must be a list")
        # Single 没有 Worker graph，因此不能声明 LIVE 协作。
        if mode == "single" and live_rows:
            raise ValueError("single planning decision cannot contain LIVE dependencies")
        # Multi Task 数量由 Runtime 上限约束，模型不能自行扩大。
        if mode == "multi" and not 1 <= len(rows) <= max_fanout_tasks:
            raise ValueError(
                f"multi planning decision requires 1-{max_fanout_tasks} tasks"
            )
        # endregion 1. Decision header 结束

        # region 2. Multi tasks：逐个校验工具集合和 step budget，再构造 typed task
        allowed = set(available_tools)
        tasks: list[PlannedTask] = []
        # 每个不可信 task mapping 分别收窄，任一非法则拒绝整个 decision。
        for row in rows:
            # 不接受字符串式 task shortcut，确保所有治理字段显式出现。
            if not isinstance(row, dict):
                raise ValueError("planned task must be an object")
            task_tools = _strings(row.get("allowed_tools"), "allowed_tools")
            unavailable = sorted(set(task_tools) - allowed)
            # Planner 只能从 Runtime 公布的工具集合中选择，不能发明 Tool。
            if unavailable:
                raise ValueError(
                    "planned task requested unavailable tools: "
                    + ", ".join(unavailable)
                )
            max_steps = row.get("max_steps", 12)
            # bool 不能冒充整数预算；具体 2..32 边界由 FanoutPlan 再校验。
            if isinstance(max_steps, bool) or not isinstance(max_steps, int):
                raise ValueError("planned task max_steps must be an integer")
            tasks.append(
                PlannedTask(
                    id=str(row.get("id") or "").strip(),
                    task=str(row.get("task") or "").strip(),
                    depends_on=tuple(_strings(row.get("depends_on"), "depends_on")),
                    write_scope=tuple(_strings(row.get("write_scope"), "write_scope")),
                    allowed_tools=tuple(task_tools),
                    acceptance_criteria=tuple(_criteria(
                        row.get("acceptance_criteria"),
                        "acceptance_criteria",
                    )),
                    max_steps=max_steps,
                )
            )
        # endregion 2. Fanout tasks 结束

        # region 3. Decision 收口：LIVE 边仍要在 FanoutPlan 中参与组合图校验
        return cls(
            mode=mode,
            reason=reason,
            global_acceptance_criteria=tuple(global_criteria),
            tasks=tuple(tasks),
            live_dependencies=tuple(
                LiveDependency.from_mapping(row) for row in live_rows
            ),
        )
        # endregion 3. Decision 收口结束

    def to_fanout_plan(
        self,
        goal: str,
    ) -> FanoutPlan:
        """把一次 Planner 提议送入唯一 ``FanoutPlan`` 确定性校验。"""

        # Single decision 没有 Task graph，不能被错误转换成 FanoutPlan。
        if self.mode != "multi":
            raise ValueError("single planning decision has no fanout plan")
        return FanoutPlan.from_mapping(
            {
                "goal": goal,
                "global_acceptance_criteria": list(self.global_acceptance_criteria),
                "tasks": [task.to_mapping() for task in self.tasks],
                "live_dependencies": [
                    dependency.to_dict() for dependency in self.live_dependencies
                ],
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """生成 planning artifact 使用的已解析、无 raw response 结构。"""

        payload = {
            "mode": self.mode,
            "reason": self.reason,
            "global_acceptance_criteria": list(self.global_acceptance_criteria),
            "tasks": [task.to_mapping() for task in self.tasks],
        }
        # 没有 LIVE 时不落空字段，保持 canonical artifact 简洁。
        if self.live_dependencies:
            payload["live_dependencies"] = [
                dependency.to_dict() for dependency in self.live_dependencies
            ]
        return payload
# endregion 2. PlanningDecision 结束


# region 3. 无状态输入 helper：只做 mapping/list/criteria 规范化
def _subagent_mapping(task: SubagentTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "task": task.task,
        "depends_on": list(task.depends_on),
        "write_scope": list(task.write_scope),
        "allowed_tools": list(task.allowed_tools),
        "acceptance_criteria": list(task.acceptance_criteria),
        "expected_artifact": task.expected_artifact,
        "max_steps": task.max_steps,
    }


def _strings(value: Any, name: str) -> list[str]:
    """规范可选字符串数组，并按首次出现顺序去重。"""

    # 缺失可选字段统一成空列表。
    if value is None:
        return []
    # 不接受单字符串或其他 iterable，避免隐式逐字符处理。
    if not isinstance(value, list):
        raise ValueError(f"planned task {name} must be a list")
    normalized = [str(item).strip() for item in value]
    # 空元素会让工具、scope 或依赖失去明确含义。
    if any(not item for item in normalized):
        raise ValueError(f"planned task {name} entries must not be empty")
    return list(dict.fromkeys(normalized))


def _criteria(value: Any, name: str) -> list[str]:
    criteria = _strings(value, name)
    # 条数上限控制 Planner/Finalizer Prompt 与 artifact 体积。
    if len(criteria) > MAX_CRITERIA:
        raise ValueError(f"{name} supports at most {MAX_CRITERIA} entries")
    # 每条标准也有独立字符上限，防止单条过大。
    if any(len(criterion) > MAX_CRITERION_CHARS for criterion in criteria):
        raise ValueError(
            f"{name} entries support at most {MAX_CRITERION_CHARS} characters"
        )
    return criteria
# endregion 3. 无状态输入 helper 结束

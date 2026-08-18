"""Adaptive execution 的最小规划契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .fanout import SubagentTask
from .live import FanoutPlan

MAX_CRITERIA = 16
MAX_CRITERION_CHARS = 500


@dataclass(frozen=True)
class PlannedTask:
    """Planner 可以提出、但必须由 Runtime 校验的一个任务。"""

    id: str
    task: str
    depends_on: list[str] = field(default_factory=list)
    write_scope: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    max_steps: int = 12

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "depends_on": list(self.depends_on),
            "write_scope": list(self.write_scope),
            "allowed_tools": list(self.allowed_tools),
            "acceptance_criteria": list(self.acceptance_criteria),
            "max_steps": self.max_steps,
        }


@dataclass(frozen=True)
class PlanningDecision:
    """Single/Fanout 策略和 Fanout 候选任务的已解析结果。"""

    mode: str
    reason: str
    global_acceptance_criteria: list[str] = field(default_factory=list)
    tasks: list[PlannedTask] = field(default_factory=list)

    @classmethod
    def from_mapping(
        cls,
        data: dict[str, Any],
        *,
        available_tools: Iterable[str],
        max_fanout_tasks: int = 16,
    ) -> "PlanningDecision":
        if not isinstance(data, dict):
            raise ValueError("planning decision must be an object")
        mode = str(data.get("mode") or "").strip().lower()
        if mode not in {"single", "fanout"}:
            raise ValueError("planning mode must be single or fanout")
        reason = str(data.get("reason") or "").strip()
        if not reason:
            raise ValueError("planning reason must not be empty")
        global_criteria = _criteria(
            data.get("global_acceptance_criteria"),
            "global_acceptance_criteria",
        )
        rows = data.get("tasks", [])
        if not isinstance(rows, list):
            raise ValueError("planning tasks must be a list")
        if mode == "single" and rows:
            raise ValueError("single planning decision must not contain fanout tasks")
        if mode == "fanout" and not 1 <= len(rows) <= max_fanout_tasks:
            raise ValueError(
                f"fanout planning decision requires 1-{max_fanout_tasks} tasks"
            )

        allowed = set(available_tools)
        tasks: list[PlannedTask] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("planned task must be an object")
            task_tools = _strings(row.get("allowed_tools"), "allowed_tools")
            unavailable = sorted(set(task_tools) - allowed)
            if unavailable:
                raise ValueError(
                    "planned task requested unavailable tools: "
                    + ", ".join(unavailable)
                )
            max_steps = row.get("max_steps", 12)
            if isinstance(max_steps, bool) or not isinstance(max_steps, int):
                raise ValueError("planned task max_steps must be an integer")
            tasks.append(
                PlannedTask(
                    id=str(row.get("id") or "").strip(),
                    task=str(row.get("task") or "").strip(),
                    depends_on=_strings(row.get("depends_on"), "depends_on"),
                    write_scope=_strings(row.get("write_scope"), "write_scope"),
                    allowed_tools=task_tools,
                    acceptance_criteria=_criteria(
                        row.get("acceptance_criteria"),
                        "acceptance_criteria",
                    ),
                    max_steps=max_steps,
                )
            )
        return cls(
            mode=mode,
            reason=reason,
            global_acceptance_criteria=global_criteria,
            tasks=tasks,
        )

    def to_fanout_plan(
        self,
        goal: str,
        *,
        completed_tasks: Iterable[SubagentTask] = (),
    ) -> FanoutPlan:
        """把模型提议送入现有 FanoutPlan 确定性校验。"""

        if self.mode != "fanout":
            raise ValueError("single planning decision has no fanout plan")
        task_rows = [_subagent_mapping(task) for task in completed_tasks]
        task_rows.extend(task.to_mapping() for task in self.tasks)
        return FanoutPlan.from_mapping(
            {
                "goal": goal,
                "global_acceptance_criteria": self.global_acceptance_criteria,
                "tasks": task_rows,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "global_acceptance_criteria": list(self.global_acceptance_criteria),
            "tasks": [task.to_mapping() for task in self.tasks],
        }


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
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"planned task {name} must be a list")
    normalized = [str(item).strip() for item in value]
    if any(not item for item in normalized):
        raise ValueError(f"planned task {name} entries must not be empty")
    return list(dict.fromkeys(normalized))


def _criteria(value: Any, name: str) -> list[str]:
    criteria = _strings(value, name)
    if len(criteria) > MAX_CRITERIA:
        raise ValueError(f"{name} supports at most {MAX_CRITERIA} entries")
    if any(len(criterion) > MAX_CRITERION_CHARS for criterion in criteria):
        raise ValueError(
            f"{name} entries support at most {MAX_CRITERION_CHARS} characters"
        )
    return criteria

"""Fanout Task、依赖图校验和确定性文件冲突规则。

本模块只保存不依赖模型、线程、Git 或持久化的 Domain 事实。Runtime 使用
readiness-driven scheduler；Domain 只校验依赖图，不预计算静态启动分组。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Iterable, Mapping


def _canonical_strings(values: Iterable[str]) -> tuple[str, ...]:
    """把直接构造传入的任意字符串 iterable 冻结为去重 tuple。"""

    normalized = tuple(str(value).strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError("fanout task collection entries must not be empty")
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True)
class SubagentTask:
    """FanoutPlan 中一个深度不可变的可调度任务。"""

    id: str
    task: str
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    write_scope: tuple[str, ...] = field(default_factory=tuple)
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    acceptance_criteria: tuple[str, ...] = field(default_factory=tuple)
    expected_artifact: str = "task_output"
    max_steps: int = 12

    def __post_init__(self) -> None:
        object.__setattr__(self, "depends_on", _canonical_strings(self.depends_on))
        object.__setattr__(self, "write_scope", _canonical_strings(self.write_scope))
        object.__setattr__(self, "allowed_tools", _canonical_strings(self.allowed_tools))
        object.__setattr__(
            self,
            "acceptance_criteria",
            _canonical_strings(self.acceptance_criteria),
        )


@dataclass(frozen=True)
class FanoutConflict:
    """一个 Candidate 无法通过确定性集成门禁的冲突事实。"""

    task_ids: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_ids", _canonical_strings(self.task_ids))


def validate_acyclic_dependencies(
    tasks: Iterable[SubagentTask],
    *,
    extra_dependencies: Mapping[str, Iterable[str]] | None = None,
) -> None:
    """验证唯一 Task identity、已知 dependency 和无环图，不返回调度层。"""

    frozen_tasks = tuple(tasks)
    by_id = {task.id: task for task in frozen_tasks}
    if len(by_id) != len(frozen_tasks):
        raise ValueError("subagent task ids must be unique")
    upstream = {task.id: set(task.depends_on) for task in frozen_tasks}
    for task_id, dependencies in (extra_dependencies or {}).items():
        if task_id not in upstream:
            raise ValueError(f"unknown dependency target: {task_id}")
        upstream[task_id].update(dependencies)
    known = set(by_id)
    unknown = sorted(
        dependency
        for dependencies in upstream.values()
        for dependency in dependencies
        if dependency not in known
    )
    if unknown:
        raise ValueError(f"unknown dependencies: {', '.join(dict.fromkeys(unknown))}")
    remaining = {task_id: set(dependencies) for task_id, dependencies in upstream.items()}
    while remaining:
        ready = [
            task.id
            for task in frozen_tasks
            if task.id in remaining and not (remaining[task.id] & set(remaining))
        ]
        if not ready:
            cycle = ", ".join(task.id for task in frozen_tasks if task.id in remaining)
            raise ValueError(f"cyclic dependencies among subagent tasks: {cycle}")
        for task_id in ready:
            remaining.pop(task_id)


def detect_write_scope_conflicts(
    tasks: Iterable[SubagentTask],
) -> list[FanoutConflict]:
    """返回声明写范围的同路径或父子路径冲突。"""

    frozen_tasks = tuple(tasks)
    conflicts: list[FanoutConflict] = []
    for left_index, left in enumerate(frozen_tasks):
        for right in frozen_tasks[left_index + 1 :]:
            overlap = _first_overlap(left.write_scope, right.write_scope)
            if overlap:
                conflicts.append(
                    FanoutConflict(
                        (left.id, right.id),
                        f"write scopes overlap: {overlap}",
                    )
                )
    return conflicts


def _first_overlap(
    left_paths: Iterable[str],
    right_paths: Iterable[str],
) -> str:
    for left in left_paths:
        for right in right_paths:
            if _paths_overlap(left, right):
                return f"{left} <-> {right}"
    return ""


def _paths_overlap(left: str, right: str) -> bool:
    left_norm = _normalize_path(left)
    right_norm = _normalize_path(right)
    if not left_norm or not right_norm:
        return False
    return (
        left_norm == right_norm
        or left_norm.startswith(f"{right_norm}/")
        or right_norm.startswith(f"{left_norm}/")
    )


def _normalize_path(path: str) -> str:
    return str(path or "").strip().strip("/").rstrip("/")

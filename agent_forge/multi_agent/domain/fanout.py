from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# 核心数据：一个可独立调度的子任务及其依赖、写范围和工具预算。
@dataclass(frozen=True)
class SubagentTask:
    """Fanout DAG 中的最小执行单元。

    ``id`` 是稳定任务键；``task`` 是 worker 目标；``depends_on`` 决定 batch；
    ``write_scope`` 用于运行前冲突隔离；``allowed_tools`` 限制 worker 工具；
    ``expected_artifact`` 声明交付物；``max_steps`` 是该 worker 的循环预算。
    """

    id: str
    task: str
    depends_on: list[str] = field(default_factory=list)
    write_scope: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    expected_artifact: str = "task_output"
    max_steps: int = 12


# 核心数据：两个或多个子任务无法安全并发/合并的明确原因。
@dataclass(frozen=True)
class FanoutConflict:
    """包含冲突任务 ID 与路径重叠或结果冲突原因。"""

    task_ids: list[str]
    reason: str


# 核心数据：注入式 fanout worker 的最小规范化结果。
@dataclass
class SubagentResult:
    """纯调度器返回的状态、输出、触碰文件和 batch 位置。"""

    task_id: str
    status: str
    output: Any = None
    touched_files: list[str] = field(default_factory=list)
    batch_index: int = 0


# 核心规则：按 depends_on 拓扑排序；未知依赖、重复 ID 和环直接失败。
def build_execution_batches(tasks: list[SubagentTask]) -> list[list[SubagentTask]]:
    """返回可并发执行的依赖层级，不处理写范围冲突。"""

    by_id = {task.id: task for task in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("subagent task ids must be unique")
    unknown_dependencies = sorted({dep for task in tasks for dep in task.depends_on if dep not in by_id})
    if unknown_dependencies:
        raise ValueError(f"unknown dependencies: {', '.join(unknown_dependencies)}")

    remaining = list(tasks)
    completed: set[str] = set()
    batches: list[list[SubagentTask]] = []
    while remaining:
        ready = [task for task in remaining if set(task.depends_on).issubset(completed)]
        if not ready:
            cycle = ", ".join(task.id for task in remaining)
            raise ValueError(f"cyclic dependencies among subagent tasks: {cycle}")
        batches.append(ready)
        ready_ids = {task.id for task in ready}
        completed |= ready_ids
        remaining = [task for task in remaining if task.id not in ready_ids]
    return batches


# 核心规则：在依赖层级内继续按 write_scope 拆分为无冲突批次。
def build_conflict_free_batches(tasks: list[SubagentTask]) -> list[list[SubagentTask]]:
    """返回同时满足 DAG 依赖和静态写范围隔离的批次。"""

    batches: list[list[SubagentTask]] = []
    for ready_level in build_execution_batches(tasks):
        level_batches: list[list[SubagentTask]] = []
        for task in ready_level:
            for batch in level_batches:
                if not detect_write_scope_conflicts([*batch, task]):
                    batch.append(task)
                    break
            else:
                level_batches.append([task])
        batches.extend(level_batches)
    return batches


# 核心规则：运行前检测声明写范围的父子路径或同路径重叠。
def detect_write_scope_conflicts(tasks: list[SubagentTask]) -> list[FanoutConflict]:
    """返回静态计划冲突；空列表表示这些 task 可并发。"""

    conflicts: list[FanoutConflict] = []
    for left_index, left in enumerate(tasks):
        for right in tasks[left_index + 1 :]:
            overlap = _first_overlap(left.write_scope, right.write_scope)
            if overlap:
                conflicts.append(
                    FanoutConflict(
                        [left.id, right.id],
                        f"write scopes overlap: {overlap}",
                    )
                )
    return conflicts


# 核心规则：运行后用真实 touched_files 再检查未声明的写冲突。
def detect_result_conflicts(results: list[SubagentResult]) -> list[FanoutConflict]:
    """返回动态结果冲突，防止错误 write_scope 静默合并。"""

    conflicts: list[FanoutConflict] = []
    for left_index, left in enumerate(results):
        for right in results[left_index + 1 :]:
            overlap = _first_overlap(left.touched_files, right.touched_files)
            if overlap:
                conflicts.append(
                    FanoutConflict(
                        [left.task_id, right.task_id],
                        f"worker outputs touched overlapping paths: {overlap}",
                    )
                )
    return conflicts


def _first_overlap(left_paths: list[str], right_paths: list[str]) -> str:
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

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class SubagentTask:
    """One task that can be scheduled for a subagent-style worker."""

    id: str
    task: str
    depends_on: list[str] = field(default_factory=list)
    write_scope: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    expected_artifact: str = "task_output"
    max_steps: int = 12


@dataclass(frozen=True)
class FanoutConflict:
    """A conflict that requires a serial or human merge step."""

    task_ids: list[str]
    reason: str


@dataclass
class SubagentResult:
    """Normalized result from one subagent task."""

    task_id: str
    status: str
    output: Any = None
    touched_files: list[str] = field(default_factory=list)
    batch_index: int = 0


@dataclass
class FanoutResult:
    """Top-level fan-out execution summary."""

    status: str
    batches: list[list[SubagentTask]]
    results: list[SubagentResult] = field(default_factory=list)
    conflicts: list[FanoutConflict] = field(default_factory=list)


Runner = Callable[[SubagentTask], Any]


def run_fanout(tasks: list[SubagentTask], runner: Runner, max_workers: int = 4) -> FanoutResult:
    """Run dependency batches concurrently when their declared write scopes do not overlap."""

    batches = build_execution_batches(tasks)
    all_results: list[SubagentResult] = []
    for batch_index, batch in enumerate(batches):
        static_conflicts = detect_write_scope_conflicts(batch)
        if static_conflicts:
            return FanoutResult("conflict_resolution_required", batches, all_results, static_conflicts)

        batch_results = _run_batch(batch, runner, max_workers=max_workers, batch_index=batch_index)
        dynamic_conflicts = detect_result_conflicts(batch_results)
        all_results.extend(batch_results)
        if dynamic_conflicts:
            return FanoutResult("conflict_resolution_required", batches, all_results, dynamic_conflicts)

    return FanoutResult("completed", batches, all_results, [])


def build_execution_batches(tasks: list[SubagentTask]) -> list[list[SubagentTask]]:
    """Topologically group tasks into parallel-ready batches."""

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


def build_conflict_free_batches(tasks: list[SubagentTask]) -> list[list[SubagentTask]]:
    """Split topological levels so declared write overlaps never run together."""

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


def detect_write_scope_conflicts(tasks: list[SubagentTask]) -> list[FanoutConflict]:
    """Find overlapping declared write scopes inside one parallel batch."""

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


def detect_result_conflicts(results: list[SubagentResult]) -> list[FanoutConflict]:
    """Find overlapping touched files produced by workers in the same batch."""

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


def _run_batch(batch: list[SubagentTask], runner: Runner, max_workers: int, batch_index: int) -> list[SubagentResult]:
    workers = max(1, min(max_workers, len(batch)))
    results_by_id: dict[str, SubagentResult] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(runner, task): task for task in batch}
        for future in as_completed(futures):
            task = futures[future]
            try:
                output = future.result()
                results_by_id[task.id] = _normalize_result(task, output, batch_index)
            except Exception as exc:
                results_by_id[task.id] = SubagentResult(
                    task_id=task.id,
                    status="failed",
                    output=str(exc),
                    touched_files=[],
                    batch_index=batch_index,
                )
    return [results_by_id[task.id] for task in batch]


def _normalize_result(task: SubagentTask, output: Any, batch_index: int) -> SubagentResult:
    if isinstance(output, SubagentResult):
        output.batch_index = batch_index
        return output
    if isinstance(output, dict):
        return SubagentResult(
            task_id=task.id,
            status=str(output.get("status") or "completed"),
            output=output,
            touched_files=list(output.get("touched_files") or []),
            batch_index=batch_index,
        )
    return SubagentResult(task_id=task.id, status="completed", output=output, batch_index=batch_index)


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

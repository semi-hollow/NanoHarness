"""在领域批次和冲突规则之上执行轻量并发 fanout。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from ..domain.fanout import (
    FanoutResult,
    SubagentResult,
    SubagentTask,
    build_execution_batches,
    detect_result_conflicts,
    detect_write_scope_conflicts,
)

Runner = Callable[[SubagentTask], Any]

# 主要入口：下方定义承接该模块的核心调用。
def run_fanout(
    tasks: list[SubagentTask],
    runner: Runner,
    max_workers: int = 4,
) -> FanoutResult:
    """并发执行无静态冲突的依赖批次，并在动态冲突处停止。"""

    batches = build_execution_batches(tasks)
    all_results: list[SubagentResult] = []
    for batch_index, batch in enumerate(batches):
        static_conflicts = detect_write_scope_conflicts(batch)
        if static_conflicts:
            return FanoutResult(
                "conflict_resolution_required",
                batches,
                all_results,
                static_conflicts,
            )
        batch_results = _run_batch(
            batch,
            runner,
            max_workers=max_workers,
            batch_index=batch_index,
        )
        dynamic_conflicts = detect_result_conflicts(batch_results)
        all_results.extend(batch_results)
        if dynamic_conflicts:
            return FanoutResult(
                "conflict_resolution_required",
                batches,
                all_results,
                dynamic_conflicts,
            )
    return FanoutResult("completed", batches, all_results, [])


def _run_batch(
    batch: list[SubagentTask],
    runner: Runner,
    max_workers: int,
    batch_index: int,
) -> list[SubagentResult]:
    workers = max(1, min(max_workers, len(batch)))
    results_by_id: dict[str, SubagentResult] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(runner, task): task for task in batch}
        for future in as_completed(futures):
            task = futures[future]
            try:
                results_by_id[task.id] = _normalize_result(
                    task,
                    future.result(),
                    batch_index,
                )
            except Exception as exc:
                results_by_id[task.id] = SubagentResult(
                    task_id=task.id,
                    status="failed",
                    output=str(exc),
                    batch_index=batch_index,
                )
    return [results_by_id[task.id] for task in batch]


def _normalize_result(
    task: SubagentTask,
    output: Any,
    batch_index: int,
) -> SubagentResult:
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
    return SubagentResult(
        task_id=task.id,
        status="completed",
        output=output,
        batch_index=batch_index,
    )

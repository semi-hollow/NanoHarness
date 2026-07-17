"""Live fanout 的调度、冲突门和恢复编排。"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from agent_forge.runtime.config import RuntimeConfig

from ..domain.fanout import (
    FanoutConflict,
    SubagentResult,
    SubagentTask,
    build_conflict_free_batches,
    detect_result_conflicts,
)
from ..domain.live import (
    FanoutCheckpoint,
    FanoutPlan,
    LiveFanoutSummary,
    LiveSubagentResult,
    aggregate_live_metrics,
)
from .dependencies import LiveFanoutDependencies


class LiveFanoutCoordinator:
    """协调真实 AgentLoop worker，但不实现 Git、文件或 worker runtime。

    第一遍只读 ``run``。它拥有依赖批次、动态冲突、patch 合并顺序、恢复资格和最终
    状态；所有外部副作用均通过 ``LiveFanoutDependencies`` 完成。
    """

    def __init__(
        self,
        *,
        plan: FanoutPlan,
        base_config: RuntimeConfig,
        dependencies: LiveFanoutDependencies,
        max_workers: int = 4,
        resume_from: str | None = None,
    ) -> None:
        self.plan = plan
        self.base_config = base_config
        self.events = dependencies.events
        self.workspace = dependencies.workspace
        self.artifacts = dependencies.artifacts
        self.workers = dependencies.workers
        self.max_workers = max(1, min(int(max_workers), 8))
        self.resume_from = resume_from

    # 主要入口：按依赖批次并发 worker，校验合并 patch，再运行 finalizer。
    def run(self) -> LiveFanoutSummary:
        """执行 dependency-aware worker，并返回可审计的集成结果。"""

        started = time.monotonic()
        base_head = self.workspace.head()
        if not base_head:
            raise RuntimeError("live fanout requires a git workspace")
        has_write_tasks = any(task.write_scope for task in self.plan.tasks)
        if has_write_tasks and not self.base_config.auto_approve_writes:
            raise RuntimeError(
                "live fanout manual write approval is not recoverable across "
                "ephemeral worktrees; use single/multi mode for per-operation approval"
            )
        if has_write_tasks and self.workspace.status():
            raise RuntimeError("write fanout requires a clean integration workspace")

        batches = build_conflict_free_batches(self.plan.tasks)
        batch_ids = [[task.id for task in batch] for batch in batches]
        results: list[LiveSubagentResult] = []
        merged_task_ids: list[str] = []
        successful_ids: set[str] = set()
        conflicts: list[FanoutConflict] = []
        self.events.add(
            0,
            "LiveFanoutCoordinator",
            "fanout_start",
            plan=self.plan.to_dict(),
            batches=batch_ids,
        )

        if self.resume_from:
            recovered = self._restore_previous(base_head)
            results.extend(recovered)
            successful_ids.update(result.task_id for result in recovered)
            merged_task_ids.extend(result.task_id for result in recovered)
        self.artifacts.write_plan(self.plan)
        self._checkpoint(base_head, results, merged_task_ids, "running")

        for batch_index, batch in enumerate(batches):
            runnable = self._runnable_tasks(
                batch,
                successful_ids,
                results,
                batch_index,
            )
            if not runnable:
                self._checkpoint(base_head, results, merged_task_ids, "running")
                continue

            batch_results = self._run_batch(
                runnable,
                batch_index,
                self.workspace.diff(),
            )
            dynamic_conflicts = self._mark_dynamic_conflicts(batch_results)
            conflicts.extend(dynamic_conflicts)
            self._merge_batch(
                runnable,
                batch_results,
                successful_ids,
                merged_task_ids,
                conflicts,
            )
            results.extend(batch_results)
            self._checkpoint(base_head, results, merged_task_ids, "running")
            self.events.add(
                batch_index + 1,
                "LiveFanoutCoordinator",
                "fanout_batch_done",
                batch=[task.id for task in runnable],
                results=[result.to_dict() for result in batch_results],
                conflicts=[asdict(conflict) for conflict in dynamic_conflicts],
            )

        integration_patch_path = self.artifacts.write_integration_patch(
            self.workspace.diff()
        )
        all_successful = len(successful_ids) == len(self.plan.tasks)
        finalizer = None
        if all_successful and not conflicts:
            finalizer = self.workers.run_finalizer(self.plan.goal, results)

        final_decision = finalizer.decision if finalizer else ""
        status = _fanout_status(results, conflicts, all_successful, final_decision)
        wall_time_ms = int((time.monotonic() - started) * 1000)
        finalizer_usage = finalizer.usage_summary if finalizer else {}
        summary = LiveFanoutSummary(
            run_id=self.events.run_id,
            goal=self.plan.goal,
            status=status,
            plan_digest=self.plan.digest,
            base_head=base_head,
            batches=batch_ids,
            results=results,
            merged_task_ids=merged_task_ids,
            conflicts=conflicts,
            wall_time_ms=wall_time_ms,
            metrics=aggregate_live_metrics(
                results,
                wall_time_ms,
                max_workers=self.max_workers,
                finalizer_usage=finalizer_usage,
            ),
            final_decision=final_decision,
            final_answer=finalizer.answer if finalizer else "",
            finalizer_trace_path=finalizer.trace_path if finalizer else "",
            finalizer_usage_path=finalizer.usage_path if finalizer else "",
            finalizer_usage_summary=finalizer_usage,
            integration_patch_path=integration_patch_path,
        )
        self._checkpoint(base_head, results, merged_task_ids, status)
        self.artifacts.write_summary(summary)
        self.events.add(
            len(batches) + 2,
            "LiveFanoutCoordinator",
            "fanout_done",
            success=status == "passed",
            status=status,
            metrics=summary.metrics,
        )
        return summary

    def _runnable_tasks(
        self,
        batch: list[SubagentTask],
        successful_ids: set[str],
        results: list[LiveSubagentResult],
        batch_index: int,
    ) -> list[SubagentTask]:
        runnable: list[SubagentTask] = []
        for task in batch:
            if task.id in successful_ids:
                continue
            if set(task.depends_on).issubset(successful_ids):
                runnable.append(task)
            else:
                results.append(
                    LiveSubagentResult(
                        task_id=task.id,
                        status="blocked_dependency",
                        batch_index=batch_index,
                        error="one or more dependencies did not complete",
                    )
                )
        return runnable

    def _run_batch(
        self,
        tasks: list[SubagentTask],
        batch_index: int,
        base_patch: str,
    ) -> list[LiveSubagentResult]:
        results: dict[str, LiveSubagentResult] = {}
        workers = max(1, min(self.max_workers, len(tasks)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    self.workers.run_worker,
                    task,
                    batch_index,
                    base_patch,
                ): task
                for task in tasks
            }
            for future in as_completed(futures):
                task = futures[future]
                try:
                    results[task.id] = future.result()
                except Exception as exc:
                    results[task.id] = LiveSubagentResult(
                        task_id=task.id,
                        status="failed",
                        batch_index=batch_index,
                        error=str(exc),
                    )
        return [results[task.id] for task in tasks]

    def _mark_dynamic_conflicts(
        self,
        results: list[LiveSubagentResult],
    ) -> list[FanoutConflict]:
        conflicts = detect_result_conflicts(
            [
                SubagentResult(
                    task_id=result.task_id,
                    status=result.status,
                    touched_files=result.touched_files,
                    batch_index=result.batch_index,
                )
                for result in results
                if result.status == "completed"
            ]
        )
        conflict_ids = {
            task_id for conflict in conflicts for task_id in conflict.task_ids
        }
        for result in results:
            if result.task_id in conflict_ids:
                result.status = "dynamic_conflict"
        return conflicts

    def _merge_batch(
        self,
        tasks: list[SubagentTask],
        results: list[LiveSubagentResult],
        successful_ids: set[str],
        merged_task_ids: list[str],
        conflicts: list[FanoutConflict],
    ) -> None:
        by_id = {task.id: task for task in tasks}
        for result in results:
            task = by_id[result.task_id]
            if result.status != "completed":
                continue
            if task.write_scope:
                patch = (
                    self.artifacts.read_text(result.patch_path)
                    if result.patch_path
                    else ""
                )
                if not patch.strip():
                    result.status = "no_patch"
                    continue
                ok, detail = self.workspace.apply_patch(patch, check_only=True)
                if not ok:
                    _record_merge_conflict(
                        result,
                        conflicts,
                        f"patch apply check failed: {detail}",
                    )
                    continue
                ok, detail = self.workspace.apply_patch(patch, check_only=False)
                if not ok:
                    _record_merge_conflict(
                        result,
                        conflicts,
                        f"patch apply failed: {detail}",
                    )
                    continue
            successful_ids.add(result.task_id)
            merged_task_ids.append(result.task_id)

    def _restore_previous(self, base_head: str) -> list[LiveSubagentResult]:
        if not self.resume_from:
            return []
        data = self.artifacts.load_resume(self.resume_from)
        if data.get("plan_digest") != self.plan.digest:
            raise RuntimeError("fanout resume plan digest does not match")
        if data.get("base_head") != base_head:
            raise RuntimeError("fanout resume base commit does not match")
        by_id = {
            str(item.get("task_id")): item
            for item in data.get("results", [])
            if isinstance(item, dict)
        }
        merged_ids = set(data.get("merged_task_ids") or [])
        known_ids = {task.id for task in self.plan.tasks}
        unknown_ids = sorted(merged_ids - known_ids)
        if unknown_ids:
            raise RuntimeError(
                f"fanout resume contains unknown merged tasks: {', '.join(unknown_ids)}"
            )

        prepared: list[tuple[SubagentTask, LiveSubagentResult, str]] = []
        for task in self.plan.tasks:
            if task.id not in merged_ids:
                continue
            item = by_id.get(task.id)
            if not item:
                raise RuntimeError(
                    f"fanout resume has no result for merged task: {task.id}"
                )
            if item.get("status") != "completed":
                raise RuntimeError(
                    f"fanout resume merged task is not completed: {task.id}"
                )
            restored_item = dict(item)
            restored_item["resumed"] = True
            result = LiveSubagentResult(**restored_item)
            patch = ""
            if task.write_scope:
                patch_path = str(item.get("patch_path") or "")
                if not patch_path:
                    raise RuntimeError("fanout resume patch is missing")
                try:
                    patch = self.artifacts.read_text(patch_path)
                except FileNotFoundError as exc:
                    raise RuntimeError(
                        f"fanout resume patch is missing: {patch_path}"
                    ) from exc
                expected_digest = str(item.get("patch_sha256") or "")
                actual_digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
                if not expected_digest or actual_digest != expected_digest:
                    raise RuntimeError(
                        f"fanout resume patch digest does not match for {task.id}"
                    )
            prepared.append((task, result, patch))

        patches = [(task.id, patch) for task, _, patch in prepared if patch]
        if patches:
            combined_patch = self.workers.validate_recovery_patches(patches)
            ok, detail = self.workspace.apply_patch(combined_patch, check_only=True)
            if not ok:
                raise RuntimeError(f"fanout resume integration check failed: {detail}")
            ok, detail = self.workspace.apply_patch(combined_patch, check_only=False)
            if not ok:
                raise RuntimeError(f"fanout resume integration failed: {detail}")
        return [result for _, result, _ in prepared]

    def _checkpoint(
        self,
        base_head: str,
        results: list[LiveSubagentResult],
        merged_task_ids: list[str],
        status: str,
    ) -> None:
        self.artifacts.write_checkpoint(
            FanoutCheckpoint(
                plan_digest=self.plan.digest,
                base_head=base_head,
                results=results,
                merged_task_ids=merged_task_ids,
                status=status,
            )
        )


def _record_merge_conflict(
    result: LiveSubagentResult,
    conflicts: list[FanoutConflict],
    detail: str,
) -> None:
    result.status = "merge_conflict"
    result.error = detail
    conflicts.append(FanoutConflict([result.task_id], detail))


def _fanout_status(
    results: list[LiveSubagentResult],
    conflicts: list[FanoutConflict],
    all_successful: bool,
    final_decision: str,
) -> str:
    conflict_statuses = {"scope_violation", "dynamic_conflict", "merge_conflict"}
    if conflicts or any(result.status in conflict_statuses for result in results):
        return "conflict_resolution_required"
    if not all_successful:
        return "partial_failure"
    if final_decision == "PASS":
        return "passed"
    return "needs_revision"

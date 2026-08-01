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

    第一遍只读 ``run``。它拥有依赖批次、动态冲突、diff 合并顺序、恢复资格和最终
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

    # 主要入口：按依赖批次并发 worker，校验合并 candidate diff，再运行 finalizer。
    def run(self) -> LiveFanoutSummary:
        """执行 dependency-aware worker，并返回可审计的集成结果。"""

        # region 准备区（首遍可折叠）：固定 base revision 并验证集成前提
        started_at = time.monotonic()
        base_revision = self.workspace.head()
        if not base_revision:
            raise RuntimeError("live fanout requires a git workspace")
        contains_write_tasks = any(task.write_scope for task in self.plan.tasks)
        if contains_write_tasks and not self.base_config.auto_approve_writes:
            raise RuntimeError(
                "live fanout manual write approval is not recoverable across "
                "ephemeral worktrees; use single/multi mode for per-operation approval"
            )
        if contains_write_tasks and self.workspace.status():
            raise RuntimeError("write fanout requires a clean integration workspace")

        # 调度视图：依赖 DAG 被切成可并发、且写集合不冲突的批次。
        dependency_batches = build_conflict_free_batches(self.plan.tasks)
        batch_task_ids = [[task.id for task in batch] for batch in dependency_batches]
        # 运行账本：四个容器分别记录结果、合并顺序、依赖完成状态和冲突事实。
        all_worker_results: list[LiveSubagentResult] = []
        merged_task_ids: list[str] = []
        successful_task_ids: set[str] = set()
        detected_conflicts: list[FanoutConflict] = []
        # endregion 准备区结束
        # region 2. 依赖调度与批次执行（主链）：恢复历史结果，同批并发，批间串行合并
        self._record_fanout_started(
            batch_task_ids=batch_task_ids,
        )

        if self.resume_from:
            restored_results = self._restore_previous(base_revision)
            all_worker_results.extend(restored_results)
            successful_task_ids.update(
                worker_result.task_id for worker_result in restored_results
            )
            merged_task_ids.extend(
                worker_result.task_id for worker_result in restored_results
            )
        self.artifacts.write_plan(self.plan)
        self._checkpoint(
            base_revision,
            all_worker_results,
            merged_task_ids,
            "running",
        )

        # 执行区：同一批并发，批次之间按依赖顺序串行并合并 candidate diff。
        for batch_index, batch in enumerate(dependency_batches):
            runnable_tasks = self._runnable_tasks(
                batch,
                successful_task_ids,
                all_worker_results,
                batch_index,
            )
            if not runnable_tasks:
                self._checkpoint(
                    base_revision,
                    all_worker_results,
                    merged_task_ids,
                    "running",
                )
                continue

            completed_batch_results = self._run_batch(
                runnable_tasks,
                batch_index,
                self.workspace.diff(),
            )
            batch_conflicts = self._mark_dynamic_conflicts(completed_batch_results)
            detected_conflicts.extend(batch_conflicts)
            self._merge_batch(
                runnable_tasks,
                completed_batch_results,
                successful_task_ids,
                merged_task_ids,
                detected_conflicts,
            )
            all_worker_results.extend(completed_batch_results)
            self._checkpoint(
                base_revision,
                all_worker_results,
                merged_task_ids,
                "running",
            )
            self._record_fanout_batch_completed(
                step=batch_index + 1,
                runnable_tasks=runnable_tasks,
                completed_batch_results=completed_batch_results,
                batch_conflicts=batch_conflicts,
            )
        # endregion 2. 依赖调度与批次执行结束

        # region 3. Finalizer 与证据发布（主链）：只有全部成功且无冲突才允许最终判定
        integrated_diff_file = self.artifacts.write_integrated_diff(
            self.workspace.diff()
        )
        every_task_succeeded = len(successful_task_ids) == len(self.plan.tasks)
        finalizer_result = None
        if every_task_succeeded and not detected_conflicts:
            finalizer_result = self.workers.run_finalizer(
                self.plan.goal,
                all_worker_results,
            )

        finalizer_decision = finalizer_result.decision if finalizer_result else ""
        fanout_status = _fanout_status(
            worker_results=all_worker_results,
            detected_conflicts=detected_conflicts,
            every_task_succeeded=every_task_succeeded,
            finalizer_decision=finalizer_decision,
        )
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        finalizer_usage_summary = (
            finalizer_result.usage_summary if finalizer_result else {}
        )
        fanout_summary = LiveFanoutSummary(
            run_id=self.events.run_id,
            goal=self.plan.goal,
            status=fanout_status,
            plan_digest=self.plan.digest,
            base_head=base_revision,
            batches=batch_task_ids,
            results=all_worker_results,
            merged_task_ids=merged_task_ids,
            conflicts=detected_conflicts,
            wall_time_ms=elapsed_ms,
            metrics=aggregate_live_metrics(
                all_worker_results,
                elapsed_ms,
                max_workers=self.max_workers,
                finalizer_usage=finalizer_usage_summary,
            ),
            final_decision=finalizer_decision,
            final_answer=finalizer_result.answer if finalizer_result else "",
            finalizer_trace_path=(
                finalizer_result.trace_path if finalizer_result else ""
            ),
            finalizer_usage_path=(
                finalizer_result.usage_path if finalizer_result else ""
            ),
            finalizer_usage_summary=finalizer_usage_summary,
            integrated_diff_path=integrated_diff_file,
        )
        self._checkpoint(
            base_revision,
            all_worker_results,
            merged_task_ids,
            fanout_status,
        )
        self.artifacts.write_summary(fanout_summary)
        self._record_fanout_completed(
            step=len(dependency_batches) + 2,
            fanout_summary=fanout_summary,
        )
        # endregion 3. Finalizer 与证据发布结束
        return fanout_summary

    # region 证据记录器（首次阅读可折叠）
    def _record_fanout_started(self, *, batch_task_ids: list[list[str]]) -> None:
        """记录固定后的计划和依赖批次，不代表 worker 已经执行。"""

        self.events.add(
            0,
            "LiveFanoutCoordinator",
            "fanout_start",
            plan=self.plan.to_dict(),
            batches=batch_task_ids,
        )

    def _record_fanout_batch_completed(
        self,
        *,
        step: int,
        runnable_tasks: list[SubagentTask],
        completed_batch_results: list[LiveSubagentResult],
        batch_conflicts: list[FanoutConflict],
    ) -> None:
        """记录一个并发批次的 worker 结果和动态冲突。"""

        self.events.add(
            step,
            "LiveFanoutCoordinator",
            "fanout_batch_done",
            batch=[task.id for task in runnable_tasks],
            results=[
                worker_result.to_dict() for worker_result in completed_batch_results
            ],
            conflicts=[asdict(conflict) for conflict in batch_conflicts],
        )

    def _record_fanout_completed(
        self,
        *,
        step: int,
        fanout_summary: LiveFanoutSummary,
    ) -> None:
        """记录 Fanout 的最终治理状态和聚合指标。"""

        self.events.add(
            step,
            "LiveFanoutCoordinator",
            "fanout_done",
            success=fanout_summary.status == "passed",
            status=fanout_summary.status,
            metrics=fanout_summary.metrics,
        )

    # endregion 证据记录器结束

    # region 调度、合并与恢复细节（首次阅读可折叠）
    def _runnable_tasks(
        self,
        batch: list[SubagentTask],
        successful_task_ids: set[str],
        all_worker_results: list[LiveSubagentResult],
        batch_index: int,
    ) -> list[SubagentTask]:
        """返回依赖已完成的任务，并为未满足依赖的任务落一条结果。"""

        runnable_tasks: list[SubagentTask] = []
        for task in batch:
            if task.id in successful_task_ids:
                continue
            if set(task.depends_on).issubset(successful_task_ids):
                runnable_tasks.append(task)
            else:
                all_worker_results.append(
                    LiveSubagentResult(
                        task_id=task.id,
                        status="blocked_dependency",
                        batch_index=batch_index,
                        error="one or more dependencies did not complete",
                    )
                )
        return runnable_tasks

    def _run_batch(
        self,
        tasks: list[SubagentTask],
        batch_index: int,
        base_diff_text: str,
    ) -> list[LiveSubagentResult]:
        """并发执行一个批次，并恢复为原任务顺序返回结果。"""

        # 并发收集容器：future 完成顺序不稳定，所以先按 task_id 建索引。
        results_by_task_id: dict[str, LiveSubagentResult] = {}
        worker_count = max(1, min(self.max_workers, len(tasks)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            task_by_worker_future = {
                executor.submit(
                    self.workers.run_worker,
                    task,
                    batch_index,
                    base_diff_text,
                ): task
                for task in tasks
            }
            for future in as_completed(task_by_worker_future):
                task = task_by_worker_future[future]
                try:
                    results_by_task_id[task.id] = future.result()
                except Exception as exc:
                    results_by_task_id[task.id] = LiveSubagentResult(
                        task_id=task.id,
                        status="failed",
                        batch_index=batch_index,
                        error=str(exc),
                    )
        return [results_by_task_id[task.id] for task in tasks]

    def _mark_dynamic_conflicts(
        self,
        batch_results: list[LiveSubagentResult],
    ) -> list[FanoutConflict]:
        """把 worker 实际触碰文件形成的动态冲突写回任务状态。"""

        detected_conflicts = detect_result_conflicts(
            [
                SubagentResult(
                    task_id=worker_result.task_id,
                    status=worker_result.status,
                    touched_files=worker_result.touched_files,
                    batch_index=worker_result.batch_index,
                )
                for worker_result in batch_results
                if worker_result.status == "completed"
            ]
        )
        conflicting_task_ids = {
            task_id for conflict in detected_conflicts for task_id in conflict.task_ids
        }
        for worker_result in batch_results:
            if worker_result.task_id in conflicting_task_ids:
                worker_result.status = "dynamic_conflict"
        return detected_conflicts

    def _merge_batch(
        self,
        tasks: list[SubagentTask],
        batch_results: list[LiveSubagentResult],
        successful_task_ids: set[str],
        merged_task_ids: list[str],
        detected_conflicts: list[FanoutConflict],
    ) -> None:
        """按稳定任务顺序校验并应用本批次 candidate diff。"""

        task_by_id = {task.id: task for task in tasks}
        for worker_result in batch_results:
            task = task_by_id[worker_result.task_id]
            if worker_result.status != "completed":
                continue
            if task.write_scope:
                candidate_diff_text = (
                    self.artifacts.read_text(worker_result.candidate_diff_path)
                    if worker_result.candidate_diff_path
                    else ""
                )
                if not candidate_diff_text.strip():
                    worker_result.status = "no_patch"
                    continue
                diff_is_applicable, applicability_detail = (
                    self.workspace.apply_unified_diff(
                        candidate_diff_text,
                        check_only=True,
                    )
                )
                if not diff_is_applicable:
                    _record_merge_conflict(
                        worker_result=worker_result,
                        detected_conflicts=detected_conflicts,
                        failure_detail=(
                            f"candidate diff apply check failed: {applicability_detail}"
                        ),
                    )
                    continue
                diff_was_applied, application_detail = (
                    self.workspace.apply_unified_diff(
                        candidate_diff_text,
                        check_only=False,
                    )
                )
                if not diff_was_applied:
                    _record_merge_conflict(
                        worker_result=worker_result,
                        detected_conflicts=detected_conflicts,
                        failure_detail=(
                            f"candidate diff apply failed: {application_detail}"
                        ),
                    )
                    continue
            successful_task_ids.add(worker_result.task_id)
            merged_task_ids.append(worker_result.task_id)

    def _restore_previous(self, base_revision: str) -> list[LiveSubagentResult]:
        """校验 checkpoint 与 diff 摘要后，重放已完成任务的集成结果。"""

        if not self.resume_from:
            return []
        resume_payload = self.artifacts.load_resume(self.resume_from)
        if resume_payload.get("plan_digest") != self.plan.digest:
            raise RuntimeError("fanout resume plan digest does not match")
        if resume_payload.get("base_head") != base_revision:
            raise RuntimeError("fanout resume base commit does not match")
        previous_result_by_task_id = {
            str(saved_result.get("task_id")): saved_result
            for saved_result in resume_payload.get("results", [])
            if isinstance(saved_result, dict)
        }
        previously_merged_task_ids = set(resume_payload.get("merged_task_ids") or [])
        planned_task_ids = {task.id for task in self.plan.tasks}
        unknown_merged_task_ids = sorted(previously_merged_task_ids - planned_task_ids)
        if unknown_merged_task_ids:
            raise RuntimeError(
                "fanout resume contains unknown merged tasks: "
                f"{', '.join(unknown_merged_task_ids)}"
            )

        # 恢复容器：每项同时保存任务定义、结果快照和待重放 candidate diff。
        prepared_recovery_items: list[tuple[SubagentTask, LiveSubagentResult, str]] = []
        for task in self.plan.tasks:
            if task.id not in previously_merged_task_ids:
                continue
            previous_result_payload = previous_result_by_task_id.get(task.id)
            if not previous_result_payload:
                raise RuntimeError(
                    f"fanout resume has no result for merged task: {task.id}"
                )
            if previous_result_payload.get("status") != "completed":
                raise RuntimeError(
                    f"fanout resume merged task is not completed: {task.id}"
                )
            restored_result_payload = dict(previous_result_payload)
            restored_result_payload["resumed"] = True
            restored_result = LiveSubagentResult(**restored_result_payload)
            candidate_diff_text = ""
            if task.write_scope:
                candidate_diff_path = str(
                    previous_result_payload.get("candidate_diff_path") or ""
                )
                if not candidate_diff_path:
                    raise RuntimeError("fanout resume candidate diff is missing")
                try:
                    candidate_diff_text = self.artifacts.read_text(candidate_diff_path)
                except FileNotFoundError as exc:
                    raise RuntimeError(
                        "fanout resume candidate diff is missing: "
                        f"{candidate_diff_path}"
                    ) from exc
                expected_digest = str(
                    previous_result_payload.get("candidate_diff_sha256") or ""
                )
                actual_digest = hashlib.sha256(
                    candidate_diff_text.encode("utf-8")
                ).hexdigest()
                if not expected_digest or actual_digest != expected_digest:
                    raise RuntimeError(
                        "fanout resume candidate diff digest does not match "
                        f"for {task.id}"
                    )
            prepared_recovery_items.append((task, restored_result, candidate_diff_text))

        recovery_diffs = [
            (task.id, candidate_diff_text)
            for task, _, candidate_diff_text in prepared_recovery_items
            if candidate_diff_text
        ]
        if recovery_diffs:
            combined_diff = self.workers.validate_recovery_diffs(recovery_diffs)
            diff_is_applicable, applicability_detail = (
                self.workspace.apply_unified_diff(
                    combined_diff,
                    check_only=True,
                )
            )
            if not diff_is_applicable:
                raise RuntimeError(
                    f"fanout resume integration check failed: {applicability_detail}"
                )
            diff_was_applied, application_detail = self.workspace.apply_unified_diff(
                combined_diff,
                check_only=False,
            )
            if not diff_was_applied:
                raise RuntimeError(
                    f"fanout resume integration failed: {application_detail}"
                )
        return [
            restored_worker_result
            for _, restored_worker_result, _ in prepared_recovery_items
        ]

    def _checkpoint(
        self,
        base_head: str,
        worker_results: list[LiveSubagentResult],
        merged_task_ids: list[str],
        fanout_status: str,
    ) -> None:
        """持久化 Fanout 的可恢复进度，不代表整个计划已经成功。"""

        self.artifacts.write_checkpoint(
            FanoutCheckpoint(
                plan_digest=self.plan.digest,
                base_head=base_head,
                results=worker_results,
                merged_task_ids=merged_task_ids,
                status=fanout_status,
            )
        )

    # endregion 调度、合并与恢复细节结束


def _record_merge_conflict(
    *,
    worker_result: LiveSubagentResult,
    detected_conflicts: list[FanoutConflict],
    failure_detail: str,
) -> None:
    """把某个 worker 的合并失败同时写回结果和冲突集合。"""

    worker_result.status = "merge_conflict"
    worker_result.error = failure_detail
    detected_conflicts.append(FanoutConflict([worker_result.task_id], failure_detail))


def _fanout_status(
    *,
    worker_results: list[LiveSubagentResult],
    detected_conflicts: list[FanoutConflict],
    every_task_succeeded: bool,
    finalizer_decision: str,
) -> str:
    """根据冲突、任务完成度和 finalizer 决策计算最终 Fanout 状态。"""

    conflict_statuses = {"scope_violation", "dynamic_conflict", "merge_conflict"}
    if detected_conflicts or any(
        worker_result.status in conflict_statuses for worker_result in worker_results
    ):
        return "conflict_resolution_required"
    if not every_task_succeeded:
        return "partial_failure"
    if finalizer_decision == "PASS":
        return "passed"
    return "needs_revision"

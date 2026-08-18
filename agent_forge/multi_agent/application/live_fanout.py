"""Live fanout 的确定性调度、受限恢复和一次剩余任务重规划。"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, fields
from typing import Any

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
    WorkerHandoff,
    aggregate_live_metrics,
    project_worker_handoff,
)
from ..domain.planning import PlanningDecision
from .dependencies import LiveFanoutDependencies

FAIL_CLOSED_STATUSES = {
    "scope_violation",
    "dynamic_conflict",
    "merge_conflict",
    "merge_recovery_failed",
}


class LiveFanoutCoordinator:
    """运行已验证 DAG；模型不能绕过 scope、merge 或 retry 边界。"""

    def __init__(
        self,
        *,
        plan: FanoutPlan,
        base_config: RuntimeConfig,
        dependencies: LiveFanoutDependencies,
        max_workers: int = 4,
        resume_from: str | None = None,
        allow_replan: bool = True,
    ) -> None:
        self.plan = plan
        self.base_config = base_config
        self.events = dependencies.events
        self.workspace = dependencies.workspace
        self.artifacts = dependencies.artifacts
        self.workers = dependencies.workers
        self.replanner = dependencies.replanner
        self.max_workers = max(1, min(int(max_workers), 8))
        self.resume_from = resume_from
        self.allow_replan = allow_replan and not bool(resume_from)

    def run(self) -> LiveFanoutSummary:
        """执行 Worker、集成、一次有界恢复和只读最终验收。"""

        started_at = time.monotonic()
        # region 1. 固定计划身份并恢复已合并前缀
        # Resume 先验证 initial/effective plan 身份，再重放已合并 Diff；不会调用 Planner。
        base_revision = self.workspace.head()
        self._validate_run_preconditions(base_revision)

        initial_identity = {"digest": self.plan.digest, "goal": self.plan.goal}
        effective_plan = self.plan
        current_results: dict[str, LiveSubagentResult] = {}
        attempt_results: list[LiveSubagentResult] = []
        successful_task_ids: set[str] = set()
        merged_task_ids: list[str] = []
        detected_conflicts: list[FanoutConflict] = []
        batch_history: list[list[str]] = []
        attempt_counts: dict[str, int] = {}
        replan_round = 0

        if self.resume_from:
            (
                effective_plan,
                restored_results,
                restored_attempts,
                replan_round,
            ) = self._restore_previous(base_revision)
            for restored_worker_result in restored_results:
                current_results[restored_worker_result.task_id] = restored_worker_result
                successful_task_ids.add(restored_worker_result.task_id)
                merged_task_ids.append(restored_worker_result.task_id)
            attempt_results.extend(restored_attempts)
            for restored_attempt in restored_attempts:
                attempt_counts[restored_attempt.task_id] = max(
                    attempt_counts.get(restored_attempt.task_id, 0),
                    restored_attempt.attempt,
                )

        self.artifacts.write_plan(self.plan)
        self._record_fanout_started(effective_plan)
        self._checkpoint(
            base_revision,
            initial_identity,
            effective_plan,
            current_results,
            attempt_results,
            merged_task_ids,
            replan_round,
            "running",
        )
        # endregion 1. 固定计划身份并恢复已合并前缀结束

        # region 2. 执行 DAG、一次 Worker retry 和一次 remaining-plan replan
        while True:
            dependency_batches = build_conflict_free_batches(effective_plan.tasks)
            pass_executed_task = False
            for batch_index, batch in enumerate(dependency_batches):
                runnable = [
                    task
                    for task in batch
                    if task.id not in successful_task_ids
                    and set(task.depends_on).issubset(successful_task_ids)
                ]
                if not runnable:
                    continue
                pass_executed_task = True
                batch_history.append([task.id for task in runnable])
                base_diff = self.workspace.diff()
                dependency_handoffs = {
                    task.id: self._dependency_handoffs(task, current_results)
                    for task in runnable
                }
                batch_results = self._run_batch(
                    runnable,
                    batch_index,
                    base_diff,
                    dependency_handoffs,
                    attempt_counts,
                )
                for batch_worker_result in batch_results:
                    attempt_results.append(batch_worker_result)
                    current_results[batch_worker_result.task_id] = batch_worker_result

                # Runtime retryability only comes from worker Trace evidence.
                for index, task in enumerate(runnable):
                    initial_worker_result = batch_results[index]
                    if not self._worker_retry_allowed(initial_worker_result):
                        continue
                    self._record_worker_retry(
                        step=batch_index + 1,
                        task_id=task.id,
                        prior_attempt=initial_worker_result.attempt,
                        failure_kind=initial_worker_result.failure_kind,
                    )
                    retried = self._run_worker_attempt(
                        task,
                        batch_index,
                        base_diff,
                        dependency_handoffs[task.id],
                        attempt_counts,
                    )
                    attempt_results.append(retried)
                    current_results[task.id] = retried
                    batch_results[index] = retried

                batch_conflicts = self._mark_dynamic_conflicts(batch_results)
                detected_conflicts.extend(batch_conflicts)
                self._merge_batch(
                    tasks=runnable,
                    batch_results=batch_results,
                    batch_index=batch_index,
                    current_results=current_results,
                    attempt_results=attempt_results,
                    attempt_counts=attempt_counts,
                    successful_task_ids=successful_task_ids,
                    merged_task_ids=merged_task_ids,
                    detected_conflicts=detected_conflicts,
                )
                self._checkpoint(
                    base_revision,
                    initial_identity,
                    effective_plan,
                    current_results,
                    attempt_results,
                    merged_task_ids,
                    replan_round,
                    "running",
                )
                self._record_fanout_batch_completed(
                    batch_index + 1,
                    runnable,
                    batch_results,
                    batch_conflicts,
                )

            unfinished = [
                task
                for task in effective_plan.tasks
                if task.id not in successful_task_ids
            ]
            if not unfinished:
                break
            self._materialize_blocked_dependencies(
                effective_plan,
                current_results,
                successful_task_ids,
                attempt_results,
                attempt_counts,
            )
            if not self._replan_allowed(
                unfinished,
                current_results,
                attempt_results,
                replan_round,
            ):
                break
            try:
                effective_plan = self._replan_remaining(
                    effective_plan,
                    current_results,
                    successful_task_ids,
                )
            except Exception as exc:
                self._record_replan_failure(
                    step=len(batch_history) + 1,
                    error=str(exc),
                )
                break
            replan_round = 1
            current_results = {
                task_id: current_worker_result
                for task_id, current_worker_result in current_results.items()
                if task_id in successful_task_ids
            }
            detected_conflicts = []
            self._checkpoint(
                base_revision,
                initial_identity,
                effective_plan,
                current_results,
                attempt_results,
                merged_task_ids,
                replan_round,
                "running",
            )
            if not pass_executed_task:  # defensive progress bound
                break
        # endregion 2. DAG 与有界恢复结束

        # region 3. 只读 Finalizer 与 canonical evidence 发布
        ordered_results = self._ordered_results(effective_plan, current_results)
        integrated_diff_file = self.artifacts.write_integrated_diff(
            self.workspace.diff()
        )
        every_task_succeeded = all(
            task.id in successful_task_ids for task in effective_plan.tasks
        )
        finalizer_result = None
        if every_task_succeeded and not detected_conflicts:
            finalizer_result = self.workers.run_finalizer(
                effective_plan, ordered_results
            )
        finalizer_decision = finalizer_result.decision if finalizer_result else ""
        fanout_status = _fanout_status(
            worker_results=ordered_results,
            detected_conflicts=detected_conflicts,
            every_task_succeeded=every_task_succeeded,
            finalizer_decision=finalizer_decision,
        )
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        finalizer_usage = finalizer_result.usage_summary if finalizer_result else {}
        summary = LiveFanoutSummary(
            run_id=self.events.run_id,
            goal=self.plan.goal,
            status=fanout_status,
            plan_digest=self.plan.digest,
            base_head=base_revision,
            batches=batch_history,
            results=ordered_results,
            merged_task_ids=merged_task_ids,
            conflicts=detected_conflicts,
            wall_time_ms=elapsed_ms,
            metrics=aggregate_live_metrics(
                attempt_results,
                elapsed_ms,
                max_workers=self.max_workers,
                finalizer_usage=finalizer_usage,
            ),
            final_decision=finalizer_decision,
            final_answer=finalizer_result.answer if finalizer_result else "",
            finalizer_trace_path=(
                finalizer_result.trace_path if finalizer_result else ""
            ),
            finalizer_usage_path=(
                finalizer_result.usage_path if finalizer_result else ""
            ),
            finalizer_usage_summary=finalizer_usage,
            integrated_diff_path=integrated_diff_file,
            initial_plan_identity=initial_identity,
            effective_plan=effective_plan.to_dict(),
            effective_plan_digest=effective_plan.digest,
            replan_round=replan_round,
            attempt_results=attempt_results,
            criterion_results=(
                finalizer_result.criterion_results if finalizer_result else []
            ),
        )
        self._checkpoint(
            base_revision,
            initial_identity,
            effective_plan,
            current_results,
            attempt_results,
            merged_task_ids,
            replan_round,
            fanout_status,
        )
        self.artifacts.write_summary(summary)
        self._record_fanout_completed(
            step=len(batch_history) + 2,
            fanout_status=fanout_status,
            metrics=summary.metrics,
            replan_round=replan_round,
        )
        return summary
        # endregion 3. Finalizer 与 evidence 发布结束

    def _validate_run_preconditions(self, base_revision: str) -> None:
        if not base_revision:
            raise RuntimeError("live fanout requires a git workspace")
        contains_writes = any(task.write_scope for task in self.plan.tasks)
        if contains_writes and not self.base_config.auto_approve_writes:
            raise RuntimeError(
                "live fanout manual write approval is not recoverable across "
                "ephemeral worktrees; use single mode for per-operation approval"
            )
        if contains_writes and self.workspace.status():
            raise RuntimeError("write fanout requires a clean integration workspace")

    def _run_batch(
        self,
        tasks: list[SubagentTask],
        batch_index: int,
        base_diff_text: str,
        dependency_handoffs: dict[str, list[WorkerHandoff]],
        attempt_counts: dict[str, int],
    ) -> list[LiveSubagentResult]:
        worker_results_by_id: dict[str, LiveSubagentResult] = {}
        worker_count = max(1, min(self.max_workers, len(tasks)))
        attempts = {
            task.id: self._next_attempt(task.id, attempt_counts) for task in tasks
        }
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    self.workers.run_worker,
                    task,
                    batch_index,
                    base_diff_text,
                    dependency_handoffs[task.id],
                    attempts[task.id],
                ): task
                for task in tasks
            }
            for future in as_completed(futures):
                task = futures[future]
                try:
                    worker_result = future.result()
                except Exception as exc:
                    worker_result = LiveSubagentResult(
                        task_id=task.id,
                        status="failed",
                        batch_index=batch_index,
                        attempt=attempts[task.id],
                        error=str(exc),
                        failure_kind="worker_port_exception",
                    )
                    worker_result.handoff = project_worker_handoff(worker_result)
                worker_results_by_id[task.id] = worker_result
        return [worker_results_by_id[task.id] for task in tasks]

    def _run_worker_attempt(
        self,
        task: SubagentTask,
        batch_index: int,
        base_diff_text: str,
        dependency_handoffs: list[WorkerHandoff],
        attempt_counts: dict[str, int],
    ) -> LiveSubagentResult:
        attempt = self._next_attempt(task.id, attempt_counts)
        try:
            return self.workers.run_worker(
                task,
                batch_index,
                base_diff_text,
                dependency_handoffs,
                attempt,
            )
        except Exception as exc:
            failed_worker_result = LiveSubagentResult(
                task_id=task.id,
                status="failed",
                batch_index=batch_index,
                attempt=attempt,
                error=str(exc),
                failure_kind="worker_port_exception",
            )
            failed_worker_result.handoff = project_worker_handoff(failed_worker_result)
            return failed_worker_result

    @staticmethod
    def _next_attempt(task_id: str, attempt_counts: dict[str, int]) -> int:
        attempt_counts[task_id] = attempt_counts.get(task_id, 0) + 1
        return attempt_counts[task_id]

    @staticmethod
    def _worker_retry_allowed(result: LiveSubagentResult) -> bool:
        return (
            result.status != "completed"
            and result.attempt == 1
            and result.retryable
            and not _is_fail_closed_result(result)
        )

    def _mark_dynamic_conflicts(
        self,
        batch_results: list[LiveSubagentResult],
    ) -> list[FanoutConflict]:
        conflicts = detect_result_conflicts(
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
        conflicting_ids = {
            task_id for conflict in conflicts for task_id in conflict.task_ids
        }
        for worker_result in batch_results:
            if worker_result.task_id in conflicting_ids:
                worker_result.status = "dynamic_conflict"
                worker_result.unresolved_issues = [
                    "actual touched files conflict in batch"
                ]
                worker_result.handoff = project_worker_handoff(worker_result)
        return conflicts

    def _merge_batch(
        self,
        *,
        tasks: list[SubagentTask],
        batch_results: list[LiveSubagentResult],
        batch_index: int,
        current_results: dict[str, LiveSubagentResult],
        attempt_results: list[LiveSubagentResult],
        attempt_counts: dict[str, int],
        successful_task_ids: set[str],
        merged_task_ids: list[str],
        detected_conflicts: list[FanoutConflict],
    ) -> None:
        worker_result_by_id = {
            worker_result.task_id: worker_result for worker_result in batch_results
        }
        result_position_by_id = {
            worker_result.task_id: index
            for index, worker_result in enumerate(batch_results)
        }
        for task in tasks:
            worker_result = worker_result_by_id[task.id]
            current_results[task.id] = worker_result
            if worker_result.status != "completed":
                continue
            outcome, detail = self._apply_candidate(task, worker_result)
            if outcome == "merge_conflict":
                worker_result.status = "merge_conflict"
                worker_result.error = detail
                worker_result.unresolved_issues = [detail]
                worker_result.handoff = project_worker_handoff(worker_result)
                self._record_serialized_conflict_retry(
                    step=batch_index + 1,
                    task_id=task.id,
                    discarded_attempt=worker_result.attempt,
                    failure=detail,
                )
                # Old candidate is evidence only. The new worker receives the latest
                # integrated state and writes a separate attempt directory.
                fresh = self._run_worker_attempt(
                    task,
                    batch_index,
                    self.workspace.diff(),
                    self._dependency_handoffs(task, current_results),
                    attempt_counts,
                )
                attempt_results.append(fresh)
                current_results[task.id] = fresh
                worker_result_by_id[task.id] = fresh
                batch_results[result_position_by_id[task.id]] = fresh
                if fresh.status != "completed":
                    fresh.status = "merge_recovery_failed"
                    fresh.retryable = False
                    fresh.unresolved_issues = [
                        fresh.error or "serialized merge recovery worker failed"
                    ]
                    fresh.handoff = project_worker_handoff(fresh)
                    continue
                outcome, detail = self._apply_candidate(task, fresh)
                if outcome == "merge_conflict":
                    fresh.status = "merge_conflict"
                    fresh.error = detail
                    fresh.retryable = False
                    fresh.unresolved_issues = [detail]
                    fresh.handoff = project_worker_handoff(fresh)
                    detected_conflicts.append(FanoutConflict([task.id], detail))
                    continue
                worker_result = fresh
            if outcome == "no_patch":
                worker_result.status = "no_patch"
                worker_result.error = detail
                worker_result.unresolved_issues = [detail]
                worker_result.handoff = project_worker_handoff(worker_result)
                continue
            worker_result.status = "completed"
            worker_result.error = ""
            worker_result.unresolved_issues = []
            worker_result.handoff = project_worker_handoff(worker_result)
            current_results[task.id] = worker_result
            successful_task_ids.add(task.id)
            if task.id not in merged_task_ids:
                merged_task_ids.append(task.id)

    def _apply_candidate(
        self,
        task: SubagentTask,
        result: LiveSubagentResult,
    ) -> tuple[str, str]:
        if not task.write_scope:
            return "merged", ""
        candidate = (
            self.artifacts.read_text(result.candidate_diff_path)
            if result.candidate_diff_path
            else ""
        )
        if not candidate.strip():
            return "no_patch", "write task produced no candidate diff"
        applicable, detail = self.workspace.apply_unified_diff(
            candidate, check_only=True
        )
        if not applicable:
            return "merge_conflict", f"candidate diff apply check failed: {detail}"
        applied, detail = self.workspace.apply_unified_diff(candidate, check_only=False)
        if not applied:
            return "merge_conflict", f"candidate diff apply failed: {detail}"
        return "merged", ""

    def _dependency_handoffs(
        self,
        task: SubagentTask,
        current_results: dict[str, LiveSubagentResult],
    ) -> list[WorkerHandoff]:
        handoffs: list[WorkerHandoff] = []
        for dependency in task.depends_on:
            dependency_result = current_results.get(dependency)
            if dependency_result is not None and dependency_result.handoff is not None:
                handoffs.append(dependency_result.handoff)
        return handoffs

    def _materialize_blocked_dependencies(
        self,
        plan: FanoutPlan,
        current_results: dict[str, LiveSubagentResult],
        successful_task_ids: set[str],
        attempt_results: list[LiveSubagentResult],
        attempt_counts: dict[str, int],
    ) -> None:
        for task in plan.tasks:
            if task.id in successful_task_ids or task.id in current_results:
                continue
            blocked_result = LiveSubagentResult(
                task_id=task.id,
                status="blocked_dependency",
                attempt=self._next_attempt(task.id, attempt_counts),
                error="one or more dependencies did not complete",
            )
            blocked_result.handoff = project_worker_handoff(blocked_result)
            current_results[task.id] = blocked_result
            attempt_results.append(blocked_result)

    def _replan_allowed(
        self,
        unfinished: list[SubagentTask],
        current_results: dict[str, LiveSubagentResult],
        attempt_results: list[LiveSubagentResult],
        replan_round: int,
    ) -> bool:
        if self.replanner is None or not self.allow_replan or replan_round >= 1:
            return False
        unfinished_ids = {task.id for task in unfinished}
        if any(
            _is_fail_closed_result(current_results[task_id])
            for task_id in unfinished_ids
            if task_id in current_results
        ):
            return False
        retryable_history = any(
            attempt_result.task_id in unfinished_ids and attempt_result.retryable
            for attempt_result in attempt_results
        )
        exhausted_retry = any(
            sum(attempt_record.task_id == task_id for attempt_record in attempt_results)
            >= 2
            for task_id in unfinished_ids
        )
        return retryable_history and exhausted_retry

    def _replan_remaining(
        self,
        effective_plan: FanoutPlan,
        current_results: dict[str, LiveSubagentResult],
        successful_task_ids: set[str],
    ) -> FanoutPlan:
        if self.replanner is None:
            raise RuntimeError("no replanner is configured")
        completed_tasks = [
            task for task in effective_plan.tasks if task.id in successful_task_ids
        ]
        completed_handoffs = [
            current_results[task.id].handoff
            for task in completed_tasks
            if current_results[task.id].handoff is not None
        ]
        failed_results = [
            current_worker_result
            for task_id, current_worker_result in current_results.items()
            if task_id not in successful_task_ids
        ]
        self._record_replan_started(
            completed_task_ids=sorted(successful_task_ids),
            failed_task_ids=sorted(
                failed_worker_result.task_id for failed_worker_result in failed_results
            ),
        )
        proposed = self.replanner.replan(
            goal=self.plan.goal,
            current_plan=effective_plan,
            completed_handoffs=[
                handoff for handoff in completed_handoffs if handoff is not None
            ],
            failed_results=failed_results,
        )
        overlap = sorted(
            successful_task_ids.intersection(task.id for task in proposed.tasks)
        )
        if overlap:
            raise ValueError(
                "replan attempted to redefine completed tasks: " + ", ".join(overlap)
            )
        # Root acceptance criteria are frozen; a replan may change only remaining work.
        bounded = PlanningDecision(
            mode="fanout",
            reason=proposed.reason,
            global_acceptance_criteria=effective_plan.global_acceptance_criteria,
            tasks=proposed.tasks,
        )
        new_plan = bounded.to_fanout_plan(
            self.plan.goal, completed_tasks=completed_tasks
        )
        self._record_replan_success(
            effective_plan=new_plan.to_dict(),
            effective_plan_digest=new_plan.digest,
        )
        return new_plan

    def _restore_previous(
        self,
        base_revision: str,
    ) -> tuple[FanoutPlan, list[LiveSubagentResult], list[LiveSubagentResult], int]:
        if not self.resume_from:
            return self.plan, [], [], 0
        payload = self.artifacts.load_resume(self.resume_from)
        identity = payload.get("initial_plan_identity") or {}
        saved_initial_digest = str(
            identity.get("digest") if isinstance(identity, dict) else ""
        ) or str(payload.get("plan_digest") or "")
        if saved_initial_digest != self.plan.digest:
            raise RuntimeError("fanout resume plan digest does not match")
        if payload.get("base_head") != base_revision:
            raise RuntimeError("fanout resume base commit does not match")

        effective_payload = payload.get("effective_plan")
        effective_plan = (
            FanoutPlan.from_mapping(effective_payload)
            if isinstance(effective_payload, dict)
            else self.plan
        )
        expected_effective_digest = str(
            payload.get("effective_plan_digest") or effective_plan.digest
        )
        if expected_effective_digest != effective_plan.digest:
            raise RuntimeError("fanout resume effective plan digest does not match")
        replan_round = int(payload.get("replan_round") or 0)
        if replan_round not in {0, 1}:
            raise RuntimeError("fanout resume replan round is invalid")

        saved_results = {
            str(row.get("task_id")): row
            for row in payload.get("results") or []
            if isinstance(row, dict)
        }
        merged_ids = list(payload.get("merged_task_ids") or [])
        task_by_id = {task.id: task for task in effective_plan.tasks}
        unknown = sorted(set(merged_ids) - set(task_by_id))
        if unknown:
            raise RuntimeError(
                "fanout resume contains unknown merged tasks: " + ", ".join(unknown)
            )
        restored: list[LiveSubagentResult] = []
        recovery_diffs: list[tuple[str, str]] = []
        for task_id in merged_ids:
            row = saved_results.get(task_id)
            if row is None:
                raise RuntimeError(
                    f"fanout resume has no result for merged task: {task_id}"
                )
            restored_worker_result = _result_from_payload(row, resumed=True)
            if restored_worker_result.status != "completed":
                raise RuntimeError(
                    f"fanout resume merged task is not completed: {task_id}"
                )
            task = task_by_id[task_id]
            if task.write_scope:
                if not restored_worker_result.candidate_diff_path:
                    raise RuntimeError("fanout resume candidate diff is missing")
                try:
                    candidate = self.artifacts.read_text(
                        restored_worker_result.candidate_diff_path
                    )
                except FileNotFoundError as exc:
                    raise RuntimeError(
                        "fanout resume candidate diff is missing: "
                        f"{restored_worker_result.candidate_diff_path}"
                    ) from exc
                digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
                if (
                    not restored_worker_result.candidate_diff_sha256
                    or digest != restored_worker_result.candidate_diff_sha256
                ):
                    raise RuntimeError(
                        "fanout resume candidate diff digest does not match "
                        f"for {task_id}"
                    )
                recovery_diffs.append((task_id, candidate))
            restored.append(restored_worker_result)

        if recovery_diffs:
            combined = self.workers.validate_recovery_diffs(recovery_diffs)
            applicable, detail = self.workspace.apply_unified_diff(
                combined, check_only=True
            )
            if not applicable:
                raise RuntimeError(f"fanout resume integration check failed: {detail}")
            applied, detail = self.workspace.apply_unified_diff(
                combined, check_only=False
            )
            if not applied:
                raise RuntimeError(f"fanout resume integration failed: {detail}")

        attempt_rows = payload.get("attempt_results") or payload.get("results") or []
        merged_id_set = set(merged_ids)
        restored_attempts = [
            _result_from_payload(row, resumed=True)
            for row in attempt_rows
            if isinstance(row, dict) and str(row.get("task_id")) in merged_id_set
        ]
        return effective_plan, restored, restored_attempts, replan_round

    def _checkpoint(
        self,
        base_head: str,
        initial_identity: dict[str, str],
        effective_plan: FanoutPlan,
        current_results: dict[str, LiveSubagentResult],
        attempt_results: list[LiveSubagentResult],
        merged_task_ids: list[str],
        replan_round: int,
        status: str,
    ) -> None:
        self.artifacts.write_checkpoint(
            FanoutCheckpoint(
                plan_digest=self.plan.digest,
                base_head=base_head,
                results=self._ordered_results(effective_plan, current_results),
                merged_task_ids=list(merged_task_ids),
                status=status,
                initial_plan_identity=initial_identity,
                effective_plan=effective_plan,
                effective_plan_digest=effective_plan.digest,
                replan_round=replan_round,
                attempt_results=list(attempt_results),
            )
        )

    @staticmethod
    def _ordered_results(
        plan: FanoutPlan,
        current_results: dict[str, LiveSubagentResult],
    ) -> list[LiveSubagentResult]:
        return [
            current_results[task.id]
            for task in plan.tasks
            if task.id in current_results
        ]

    def _record_fanout_started(self, effective_plan: FanoutPlan) -> None:
        self.events.add(
            0,
            "LiveFanoutCoordinator",
            "fanout_start",
            initial_plan_identity={"digest": self.plan.digest, "goal": self.plan.goal},
            effective_plan=effective_plan.to_dict(),
        )

    def _record_fanout_batch_completed(
        self,
        step: int,
        tasks: list[SubagentTask],
        worker_results: list[LiveSubagentResult],
        conflicts: list[FanoutConflict],
    ) -> None:
        self.events.add(
            step,
            "LiveFanoutCoordinator",
            "fanout_batch_done",
            batch=[task.id for task in tasks],
            results=[worker_result.to_dict() for worker_result in worker_results],
            conflicts=[asdict(conflict) for conflict in conflicts],
        )

    def _record_worker_retry(
        self,
        *,
        step: int,
        task_id: str,
        prior_attempt: int,
        failure_kind: str,
    ) -> None:
        self.events.add(
            step,
            "LiveFanoutCoordinator",
            "worker_retry",
            task_id=task_id,
            prior_attempt=prior_attempt,
            failure_kind=failure_kind,
        )

    def _record_serialized_conflict_retry(
        self,
        *,
        step: int,
        task_id: str,
        discarded_attempt: int,
        failure: str,
    ) -> None:
        self.events.add(
            step,
            "LiveFanoutCoordinator",
            "serialized_conflict_retry",
            task_id=task_id,
            discarded_attempt=discarded_attempt,
            failure=failure,
        )

    def _record_replan_started(
        self,
        *,
        completed_task_ids: list[str],
        failed_task_ids: list[str],
    ) -> None:
        self.events.add(
            0,
            "LiveFanoutCoordinator",
            "replan_started",
            completed_task_ids=completed_task_ids,
            failed_task_ids=failed_task_ids,
        )

    def _record_replan_success(
        self,
        *,
        effective_plan: dict[str, Any],
        effective_plan_digest: str,
    ) -> None:
        self.events.add(
            0,
            "LiveFanoutCoordinator",
            "replan_result",
            effective_plan=effective_plan,
            effective_plan_digest=effective_plan_digest,
        )

    def _record_replan_failure(self, *, step: int, error: str) -> None:
        self.events.add(
            step,
            "LiveFanoutCoordinator",
            "replan_result",
            success=False,
            error=error,
        )

    def _record_fanout_completed(
        self,
        *,
        step: int,
        fanout_status: str,
        metrics: dict[str, Any],
        replan_round: int,
    ) -> None:
        self.events.add(
            step,
            "LiveFanoutCoordinator",
            "fanout_done",
            success=fanout_status == "passed",
            status=fanout_status,
            metrics=metrics,
            replan_round=replan_round,
        )


def _is_fail_closed_result(result: LiveSubagentResult) -> bool:
    kind = result.failure_kind.lower()
    deterministic_denial = any(
        marker in kind
        for marker in ("permission", "approval", "policy", "guardrail", "scope")
    )
    return result.status in FAIL_CLOSED_STATUSES or deterministic_denial


def _result_from_payload(
    payload: dict[str, Any],
    *,
    resumed: bool,
) -> LiveSubagentResult:
    values = dict(payload)
    handoff_payload = values.get("handoff")
    if isinstance(handoff_payload, dict):
        allowed_handoff = {field.name for field in fields(WorkerHandoff)}
        values["handoff"] = WorkerHandoff(
            **{
                key: value
                for key, value in handoff_payload.items()
                if key in allowed_handoff
            }
        )
    allowed = {field.name for field in fields(LiveSubagentResult)}
    values = {key: value for key, value in values.items() if key in allowed}
    values["resumed"] = resumed
    restored_worker_result = LiveSubagentResult(**values)
    if restored_worker_result.handoff is None:
        restored_worker_result.handoff = project_worker_handoff(restored_worker_result)
    return restored_worker_result


def _fanout_status(
    *,
    worker_results: list[LiveSubagentResult],
    detected_conflicts: list[FanoutConflict],
    every_task_succeeded: bool,
    finalizer_decision: str,
) -> str:
    if detected_conflicts or any(
        worker_result.status
        in {"scope_violation", "dynamic_conflict", "merge_conflict"}
        for worker_result in worker_results
    ):
        return "conflict_resolution_required"
    if not every_task_succeeded:
        return "partial_failure"
    if finalizer_decision == "PASS":
        return "passed"
    if finalizer_decision == "BLOCKED":
        return "blocked"
    return "needs_revision"

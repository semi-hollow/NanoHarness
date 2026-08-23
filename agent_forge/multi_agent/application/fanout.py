"""Multi-Agent 唯一执行编排器。

系统角色：接收已经通过 ``FanoutPlan`` 校验的执行图，调度隔离 Worker，并把候选
Diff 经过确定性门禁后合入集成 workspace。
输入：计划、RuntimeConfig，以及 workspace/artifact/worker/replanner Ports。
输出：``LiveFanoutSummary``、checkpoint、集成 Diff 和 Finalizer 证据。
相邻边界：Planner 只提议计划；``LiveHandoffRuntime`` 只维护协作事实；本文件唯一
拥有 Worker 生命周期、retry/replan、candidate gate 和最终集成顺序。

折叠导航：1 Public 主链；2 Worker 调度；3 Candidate 集成；4 恢复与 checkpoint；
5 Trace 证据；6 纯结果投影。先读 ``run``，需要解释 LIVE 时只展开 ``_run_live_plan``。
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, fields
from typing import Any

from agent_forge.runtime.config import RuntimeConfig

from ..domain.fanout import (
    FanoutConflict,
    SubagentResult,
    SubagentTask,
    build_conflict_free_batches,
    detect_result_conflicts,
    detect_write_scope_conflicts,
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
from .live_handoff import LiveHandoffRuntime

FAIL_CLOSED_STATUSES = {
    "scope_violation",
    "dynamic_conflict",
    "merge_conflict",
    "merge_recovery_failed",
}


class FanoutCoordinator:
    """运行已验证 DAG；模型不能绕过 scope、freshness、merge 或 retry 边界。"""

    # region 1. Public 主链：固定计划身份，执行所有 Worker，再由只读 Finalizer 收口
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
        requested_workers = int(max_workers)
        if plan.live_dependencies and requested_workers < 2:
            raise ValueError("LIVE dependencies require max_workers >= 2")
        if plan.live_dependencies and resume_from:
            raise ValueError("LIVE coordination resume is not supported in V1")
        self.max_workers = max(1, min(requested_workers, 8))
        self.resume_from = resume_from
        self.allow_replan = allow_replan and not bool(resume_from)
        self.live_handoff = (
            LiveHandoffRuntime(plan, self.artifacts)
            if plan.live_dependencies
            else None
        )

    def run(self) -> LiveFanoutSummary:
        """执行 Worker、集成、一次有界恢复和只读最终验收。

        伪代码主线：

        1. 固定 Git/Plan 身份，必要时恢复已经验证并合入的前缀。
        2. 按 HARD batches 或 LIVE dependencies 调度尚未成功的 Worker。
        3. 每轮把 candidate 过冲突与集成门；失败只允许一次 retry/replan。
        4. 所有任务成功且无冲突时，才启动只读 Finalizer。
        5. 无论成功或失败，都发布同一份 Summary、Checkpoint 和 Trace 事实。
        """

        # 从进入编排器开始计时；Worker 历史恢复成本会在 Metrics 中另行区分。
        started_at = time.monotonic()
        # region 1. 固定计划身份并恢复已合并前缀
        # Resume 先验证 initial/effective plan 身份，再重放已合并 Diff；不会调用 Planner。
        # 先读取不可变 Git 基线并做前置检查，检查失败时还没有创建任何 Worker。
        base_revision = self.workspace.head()
        self._validate_run_preconditions(base_revision)

        # 下面这些集合是整个 Run 的唯一可变状态：当前结果、全部尝试、成功和合并前缀。
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

        # 如果调用方指定 resume：只恢复已验证的成功前缀，未完成任务仍由本轮重新调度。
        if self.resume_from:
            (
                effective_plan,
                restored_results,
                restored_attempts,
                replan_round,
            ) = self._restore_previous(base_revision)
            # 把恢复结果重新放回当前状态，并视为已经成功、已经合并，避免重复执行。
            for restored_worker_result in restored_results:
                current_results[restored_worker_result.task_id] = restored_worker_result
                successful_task_ids.add(restored_worker_result.task_id)
                merged_task_ids.append(restored_worker_result.task_id)
            attempt_results.extend(restored_attempts)
            # 从历史最大 Attempt 号继续计数，防止新尝试复用旧身份或覆盖旧目录。
            for restored_attempt in restored_attempts:
                attempt_counts[restored_attempt.task_id] = max(
                    attempt_counts.get(restored_attempt.task_id, 0),
                    restored_attempt.attempt,
                )

        # 先落 initial plan 和 running checkpoint；进程中断时也能知道 Run 从哪里开始。
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
        # 外层循环代表一次 effective plan generation；正常情况只走一轮，replan 最多再走一轮。
        while True:
            # 有 LIVE 边时使用事件驱动调度，让下游能在上游完成前消费 READY/UPDATE。
            if effective_plan.live_dependencies:
                pass_executed_task = self._run_live_plan(
                    effective_plan,
                    current_results=current_results,
                    attempt_results=attempt_results,
                    attempt_counts=attempt_counts,
                    successful_task_ids=successful_task_ids,
                    merged_task_ids=merged_task_ids,
                    detected_conflicts=detected_conflicts,
                    batch_history=batch_history,
                )
            else:
                # 没有 LIVE 边时按 HARD dependency + write scope 生成可安全并行的批次。
                dependency_batches = build_conflict_free_batches(
                    effective_plan.tasks
                )
                pass_executed_task = False
                # 逐批执行：同一 batch 内并发，不同 batch 按依赖顺序推进。
                for batch_index, batch in enumerate(dependency_batches):
                    # 过滤已恢复/已成功任务，并确认所有 HARD 前置任务都已经成功。
                    runnable = [
                        task
                        for task in batch
                        if task.id not in successful_task_ids
                        and set(task.depends_on).issubset(successful_task_ids)
                    ]
                    # 当前批次没有新任务时直接跳过；它可能已由 resume 恢复完成。
                    if not runnable:
                        continue
                    pass_executed_task = True
                    batch_history.append([task.id for task in runnable])
                    # 同批 Worker 共享同一集成基线，但各自在隔离 worktree 中执行。
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
                    # 先保存每个原始 Attempt 的事实；是否能重试或合并在后面单独判断。
                    for batch_worker_result in batch_results:
                        attempt_results.append(batch_worker_result)
                        current_results[batch_worker_result.task_id] = (
                            batch_worker_result
                        )

                    # Runtime 是否允许重试，只能由 Worker Trace 中的事实证据决定。
                    # 逐个检查初次结果：不可重试就保留原结果，可重试则仅替换当前候选。
                    for index, task in enumerate(runnable):
                        initial_worker_result = batch_results[index]
                        # scope/conflict 等 fail-closed 结果不能靠自动重试绕过治理边界。
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

                    # 先检测同批 Worker 的真实 touched-files 冲突，再按计划顺序集成 candidate。
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
                    # 每个批次合并后立即 checkpoint；下一批和恢复流程只依赖 durable 前缀。
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

            # 一轮调度结束后，只根据 successful_task_ids 计算仍未完成的任务。
            unfinished = [
                task
                for task in effective_plan.tasks
                if task.id not in successful_task_ids
            ]
            # 没有剩余任务说明执行图已完成，可以离开调度循环进入 Finalizer。
            if not unfinished:
                break
            # 对 HARD 前置已失败的任务生成显式 blocked 结果，不能让它们静默消失。
            self._materialize_blocked_dependencies(
                effective_plan,
                current_results,
                successful_task_ids,
                attempt_results,
                attempt_counts,
            )
            # 没有 retryable evidence、已经 replan 过或功能关闭时，以当前失败事实收口。
            if not self._replan_allowed(
                unfinished,
                current_results,
                attempt_results,
                replan_round,
            ):
                break
            # Replanner 只替换 remaining work；异常不能破坏已经成功并合入的前缀。
            try:
                effective_plan = self._replan_remaining(
                    effective_plan,
                    current_results,
                    successful_task_ids,
                )
            except Exception as exc:
                # 计划生成或校验失败时记录原因，并保留本轮所有 Worker/候选证据。
                self._record_replan_failure(
                    step=len(batch_history) + 1,
                    error=str(exc),
                )
                break
            replan_round = 1
            # 新计划包含 LIVE 边时，创建或换代 Runtime；旧 generation 事件不能继续使用。
            if effective_plan.live_dependencies:
                # 构造器已经保护该约束；这里保留防御检查，避免未来调用路径绕过构造器。
                if self.max_workers < 2:  # pragma: no cover - constructor protects V1
                    raise ValueError("LIVE dependencies require max_workers >= 2")
                # 第一次进入 LIVE 创建状态机；已有状态机则原子切换到新 generation。
                if self.live_handoff is None:
                    self.live_handoff = LiveHandoffRuntime(
                        effective_plan,
                        self.artifacts,
                    )
                else:
                    self.live_handoff.replace_plan(effective_plan)
            else:
                # 新计划退化为 HARD-only 时移除 LIVE 状态，不保留上一代 mailbox。
                self.live_handoff = None
            # Replan 后只保留成功前缀；失败结果仍在 attempt_results 中作为历史证据。
            current_results = {
                task_id: current_worker_result
                for task_id, current_worker_result in current_results.items()
                if task_id in successful_task_ids
            }
            detected_conflicts = []
            # 在下一代 Worker 启动前持久化 effective plan 与保留的成功前缀。
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
            # 如果上一轮一个 Worker 都未启动，继续循环也不会改变状态，必须防止空转。
            if not pass_executed_task:  # 防止异常计划导致调度循环无法推进。
                break
        # endregion 2. DAG 与有界恢复结束

        # region 3. 只读 Finalizer 与 canonical evidence 发布
        # 按 effective plan 顺序投影结果，避免并发完成顺序影响报告和持久化内容。
        ordered_results = self._ordered_results(effective_plan, current_results)
        # 无论最终状态如何，都把当前 workspace Diff 固化为可审计的集成候选。
        integrated_diff_file = self.artifacts.write_integrated_diff(
            self.workspace.diff()
        )
        # Finalizer 的准入条件是“所有任务成功且没有任何已知冲突”，不是模型自述完成。
        every_task_succeeded = all(
            task.id in successful_task_ids for task in effective_plan.tasks
        )
        finalizer_result = None
        # 只有满足准入条件才运行只读验收；否则保留空 decision，由统一状态函数判失败。
        if every_task_succeeded and not detected_conflicts:
            finalizer_result = self.workers.run_finalizer(
                effective_plan, ordered_results
            )
        finalizer_decision = finalizer_result.decision if finalizer_result else ""
        # 把 Worker、Conflict、完成度和 Finalizer 四类事实集中投影为唯一 Run 终态。
        fanout_status = _fanout_status(
            worker_results=ordered_results,
            detected_conflicts=detected_conflicts,
            every_task_succeeded=every_task_succeeded,
            finalizer_decision=finalizer_decision,
        )
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        finalizer_usage = finalizer_result.usage_summary if finalizer_result else {}
        # Summary 只引用结构化结果和 canonical 路径，不嵌入 Worker 私有 Conversation。
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
        # 先写终态 checkpoint，再写报告和完成事件；三者表达同一个 fanout_status。
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
        """在创建 Worker 前拒绝脏 workspace、不可恢复审批和无 Git 基线。"""

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
    # endregion 1. Public 主链结束

    # region 2. Worker 调度：HARD 批次与 LIVE 动态启动共用同一 Worker/attempt 生命周期
    def _run_batch(
        self,
        tasks: list[SubagentTask],
        batch_index: int,
        base_diff_text: str,
        dependency_handoffs: dict[str, list[WorkerHandoff]],
        attempt_counts: dict[str, int],
    ) -> list[LiveSubagentResult]:
        """并发执行同一就绪批次，并按计划顺序返回相互隔离的 Worker 结果。"""

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

    def _run_live_plan(
        self,
        plan: FanoutPlan,
        *,
        current_results: dict[str, LiveSubagentResult],
        attempt_results: list[LiveSubagentResult],
        attempt_counts: dict[str, int],
        successful_task_ids: set[str],
        merged_task_ids: list[str],
        detected_conflicts: list[FanoutConflict],
        batch_history: list[list[str]],
    ) -> bool:
        """动态启动 LIVE target，但只在 Producer 最终集成后放行其 candidate。

        输入是当前 generation 的 plan、结果和 attempt 账本；输出只表示本轮是否真正
        启动过 Worker。启动只依赖 HARD completion 与 LIVE readiness，最终正确性则
        始终由后面的 integration freshness gate 决定。
        """

        runtime = self.live_handoff
        if runtime is None:  # pragma: no cover - caller protects this invariant
            raise AssertionError("LIVE plan requires LiveHandoffRuntime")
        task_by_id = {task.id: task for task in plan.tasks}
        pending = {
            task.id for task in plan.tasks if task.id not in successful_task_ids
        }
        running: dict[Future[LiveSubagentResult], SubagentTask] = {}
        executed = False

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while pending or running:
                # region 1. 动态启动：HARD、LIVE readiness、并发与 write-scope 全部满足
                observed_revision = runtime.state_revision
                running_tasks = list(running.values())
                launched: list[SubagentTask] = []
                for task in plan.tasks:
                    if task.id not in pending or len(running) >= self.max_workers:
                        continue
                    if not set(task.depends_on).issubset(successful_task_ids):
                        continue
                    if not runtime.live_ready(task.id):
                        continue
                    if detect_write_scope_conflicts([task, *running_tasks]):
                        continue
                    attempt = self._next_attempt(task.id, attempt_counts)
                    context = runtime.begin_attempt(task.id, attempt)
                    worker_context = context if plan.live_routes_for(task.id) else None
                    future = executor.submit(
                        self._execute_live_worker,
                        runtime,
                        task,
                        len(batch_history),
                        self.workspace.diff(),
                        self._dependency_handoffs(task, current_results),
                        attempt,
                        worker_context,
                    )
                    running[future] = task
                    running_tasks.append(task)
                    pending.remove(task.id)
                    launched.append(task)
                    executed = True
                if launched:
                    batch_history.append([task.id for task in launched])
                # endregion 1. 动态启动结束

                # region 2. 完成与 retry：Condition 唤醒，不靠轮询 sleep 决定正确性
                done = [future for future in running if future.done()]
                if not done:
                    if not running:
                        break
                    runtime.wait_for_change(observed_revision, timeout=30.0)
                    continue

                done.sort(key=lambda future: list(task_by_id).index(running[future].id))
                completed_now: list[LiveSubagentResult] = []
                for future in done:
                    task = running.pop(future)
                    completed_worker_result = future.result()
                    attempt_results.append(completed_worker_result)
                    current_results[task.id] = completed_worker_result
                    if self._worker_retry_allowed(completed_worker_result):
                        self._record_worker_retry(
                            step=len(batch_history),
                            task_id=task.id,
                            prior_attempt=completed_worker_result.attempt,
                            failure_kind=completed_worker_result.failure_kind,
                        )
                        completed_worker_result = self._run_worker_attempt(
                            task,
                            completed_worker_result.batch_index,
                            self.workspace.diff(),
                            self._dependency_handoffs(task, current_results),
                            attempt_counts,
                            live_handoff=runtime,
                        )
                        attempt_results.append(completed_worker_result)
                        current_results[task.id] = completed_worker_result
                    completed_now.append(completed_worker_result)
                # endregion 2. 完成与 retry 结束

                # region 3. 稳定集成：组合拓扑序 + Producer sealed + Consumer final version
                # 组合拓扑序也是集成顺序；LIVE target 可以提前完成，但不能提前越过门禁。
                for task_id in plan.integration_order(set(current_results)):
                    if task_id in successful_task_ids or task_id in pending:
                        continue
                    task = task_by_id[task_id]
                    current_worker_result = current_results[task_id]
                    if current_worker_result.status != "completed":
                        continue
                    if not set(task.depends_on).issubset(successful_task_ids):
                        continue
                    if not {
                        dependency.producer_task_id
                        for dependency in plan.live_dependencies_for(task_id)
                    }.issubset(successful_task_ids):
                        continue
                    self._merge_live_result(
                        runtime=runtime,
                        task=task,
                        worker_result=current_worker_result,
                        current_results=current_results,
                        attempt_results=attempt_results,
                        attempt_counts=attempt_counts,
                        successful_task_ids=successful_task_ids,
                        merged_task_ids=merged_task_ids,
                        detected_conflicts=detected_conflicts,
                    )

                self._record_fanout_batch_completed(
                    len(batch_history),
                    [
                        task_by_id[completed_worker_result.task_id]
                        for completed_worker_result in completed_now
                    ],
                    completed_now,
                    [],
                )
                # endregion 3. 稳定集成结束

        for task in plan.tasks:
            final_result = current_results.get(task.id)
            if (
                final_result is not None
                and final_result.status == "completed"
                and task.id not in successful_task_ids
            ):
                final_result.status = "stale_live_dependency"
                final_result.error = (
                    "final LIVE integration freshness barrier rejected candidate"
                )
                final_result.unresolved_issues = [final_result.error]
                final_result.handoff = project_worker_handoff(final_result)
        return executed

    def _execute_live_worker(
        self,
        runtime: LiveHandoffRuntime,
        task: SubagentTask,
        batch_index: int,
        base_diff_text: str,
        dependency_handoffs: list[WorkerHandoff],
        attempt: int,
        coordination: Any,
    ) -> LiveSubagentResult:
        """执行一个真实 Worker，并保证 future 完成前 Runtime 已观察 attempt 终态。"""

        try:
            live_worker_result = self.workers.run_worker(
                task,
                batch_index,
                base_diff_text,
                dependency_handoffs,
                attempt,
                coordination,
            )
        except Exception as exc:
            live_worker_result = LiveSubagentResult(
                task_id=task.id,
                status="failed",
                batch_index=batch_index,
                attempt=attempt,
                error=str(exc),
                failure_kind="worker_port_exception",
            )
            live_worker_result.handoff = project_worker_handoff(live_worker_result)
        runtime.finish_attempt(
            task.id,
            attempt,
            success=live_worker_result.status == "completed",
        )
        return live_worker_result

    def _merge_live_result(
        self,
        *,
        runtime: LiveHandoffRuntime,
        task: SubagentTask,
        worker_result: LiveSubagentResult,
        current_results: dict[str, LiveSubagentResult],
        attempt_results: list[LiveSubagentResult],
        attempt_counts: dict[str, int],
        successful_task_ids: set[str],
        merged_task_ids: list[str],
        detected_conflicts: list[FanoutConflict],
    ) -> None:
        """在一个原子 freshness 授权之后应用 candidate；冲突仍只恢复一次。"""

        # region 1. Freshness 授权：最终版本或 Attempt 已过期时直接拒绝集成
        # authorize_integration 是正确性边界；调度完成不等于仍有资格写入集成结果。
        try:
            runtime.authorize_integration(task.id, worker_result.attempt)
        except RuntimeError as exc:
            worker_result.status = "stale_live_dependency"
            worker_result.error = str(exc)
            worker_result.retryable = False
            worker_result.unresolved_issues = [str(exc)]
            worker_result.handoff = project_worker_handoff(worker_result)
            current_results[task.id] = worker_result
            runtime.seal_integration(task.id, worker_result.attempt, success=False)
            return
        # endregion 1. Freshness 授权

        # region 2. 候选应用与一次恢复：冲突后只允许从最新集成状态串行重跑
        # 第一次失败的 candidate 只保留作证据；恢复 Attempt 必须重新过 freshness gate。
        outcome, detail = self._apply_candidate(task, worker_result)
        if outcome == "merge_conflict":
            worker_result.status = "merge_conflict"
            worker_result.error = detail
            worker_result.unresolved_issues = [detail]
            worker_result.handoff = project_worker_handoff(worker_result)
            self._record_serialized_conflict_retry(
                step=worker_result.batch_index + 1,
                task_id=task.id,
                discarded_attempt=worker_result.attempt,
                failure=detail,
            )
            fresh = self._run_worker_attempt(
                task,
                worker_result.batch_index,
                self.workspace.diff(),
                self._dependency_handoffs(task, current_results),
                attempt_counts,
                live_handoff=runtime,
            )
            attempt_results.append(fresh)
            current_results[task.id] = fresh
            if fresh.status != "completed":
                fresh.status = "merge_recovery_failed"
                fresh.retryable = False
                fresh.unresolved_issues = [
                    fresh.error or "serialized merge recovery worker failed"
                ]
                fresh.handoff = project_worker_handoff(fresh)
                return
            try:
                runtime.authorize_integration(task.id, fresh.attempt)
            except RuntimeError as exc:
                fresh.status = "stale_live_dependency"
                fresh.error = str(exc)
                fresh.retryable = False
                fresh.unresolved_issues = [str(exc)]
                fresh.handoff = project_worker_handoff(fresh)
                runtime.seal_integration(task.id, fresh.attempt, success=False)
                return
            outcome, detail = self._apply_candidate(task, fresh)
            worker_result = fresh
        # endregion 2. 候选应用与一次恢复

        # region 3. 终态收口：失败保留 Handoff，成功再 sealed 并进入已合并集合
        if outcome != "merged":
            worker_result.status = outcome
            worker_result.error = detail
            worker_result.retryable = False
            worker_result.unresolved_issues = [detail]
            worker_result.handoff = project_worker_handoff(worker_result)
            runtime.seal_integration(
                task.id,
                worker_result.attempt,
                success=False,
            )
            if outcome == "merge_conflict":
                detected_conflicts.append(FanoutConflict([task.id], detail))
            return
        worker_result.status = "completed"
        worker_result.error = ""
        worker_result.unresolved_issues = []
        worker_result.handoff = project_worker_handoff(worker_result)
        current_results[task.id] = worker_result
        successful_task_ids.add(task.id)
        if task.id not in merged_task_ids:
            merged_task_ids.append(task.id)
        runtime.seal_integration(task.id, worker_result.attempt, success=True)
        # endregion 3. 终态收口

    def _run_worker_attempt(
        self,
        task: SubagentTask,
        batch_index: int,
        base_diff_text: str,
        dependency_handoffs: list[WorkerHandoff],
        attempt_counts: dict[str, int],
        *,
        live_handoff: LiveHandoffRuntime | None = None,
    ) -> LiveSubagentResult:
        """运行一个有编号的 Worker Attempt，并把异常统一投影为结果契约。"""

        # region 1. Attempt 身份：先递增尝试号，再绑定不可伪造的 Worker Context
        attempt = self._next_attempt(task.id, attempt_counts)
        coordination = None
        if live_handoff is not None:
            context = live_handoff.begin_attempt(task.id, attempt)
            if live_handoff.plan.live_routes_for(task.id):
                coordination = context
        # endregion 1. Attempt 身份

        # region 2. Worker Port：兼容 HARD-only 调用，并把 Adapter 异常投影为稳定结果
        # 是否启用 LIVE 只改变额外 Context，不分叉 Worker 的真实执行 substrate。
        try:
            attempted_worker_result = (
                self.workers.run_worker(
                    task,
                    batch_index,
                    base_diff_text,
                    dependency_handoffs,
                    attempt,
                    coordination,
                )
                if live_handoff is not None
                else self.workers.run_worker(
                    task,
                    batch_index,
                    base_diff_text,
                    dependency_handoffs,
                    attempt,
                )
            )
        except Exception as exc:
            attempted_worker_result = LiveSubagentResult(
                task_id=task.id,
                status="failed",
                batch_index=batch_index,
                attempt=attempt,
                error=str(exc),
                failure_kind="worker_port_exception",
            )
            attempted_worker_result.handoff = project_worker_handoff(
                attempted_worker_result
            )
        # endregion 2. Worker Port 调用

        # region 3. Attempt 终态：LIVE Runtime 只记录真实完成状态，再返回 Coordinator
        if live_handoff is not None:
            live_handoff.finish_attempt(
                task.id,
                attempt,
                success=attempted_worker_result.status == "completed",
            )
        return attempted_worker_result
        # endregion 3. Attempt 终态

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
    # endregion 2. Worker 调度结束

    # region 3. Candidate 集成：动态冲突、scope、hash、patch applicability 与 handoff
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
        """按稳定顺序合并候选结果；补丁失效时仅允许一次最新状态串行重跑。"""

        # region 1. 稳定索引：按输入任务顺序处理，不依赖并发完成顺序
        worker_result_by_id = {
            worker_result.task_id: worker_result for worker_result in batch_results
        }
        result_position_by_id = {
            worker_result.task_id: index
            for index, worker_result in enumerate(batch_results)
        }
        # endregion 1. 稳定索引

        # region 2. 顺序集成：每个候选独立经过 apply gate 与一次冲突恢复
        for task in tasks:
            worker_result = worker_result_by_id[task.id]
            current_results[task.id] = worker_result
            if worker_result.status != "completed":
                continue
            # region 2.1 Apply 与恢复：旧候选失败时从最新 workspace 重跑一次
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
                # 旧候选只作为证据保留；新 Worker 从最新集成状态启动，并写入独立尝试目录。
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
            # endregion 2.1 Apply 与恢复

            # region 2.2 接受结果：no-patch 保留失败证据，merged 才进入成功集合
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
            # endregion 2.2 接受结果
        # endregion 2. 顺序集成

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
    # endregion 3. Candidate 集成结束

    # region 4. 有界恢复与 checkpoint：只替换未完成图，HARD-only 才允许 resume
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
        """只为 remaining work 请求一次新计划，并保护已完成前缀。"""

        # region 1. Remaining-work 证据：只把已完成 Handoff 与当前失败交给 Replanner
        # Replanner 只能看稳定 Handoff/Result，不能读取 Worker 私有 Conversation。
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
        # endregion 1. Remaining-work 证据

        # region 2. 有界提案：不得重定义已完成任务，LIVE 边只能连接本代剩余任务
        # Proposed tasks 先过完成前缀与 generation 边界，再允许成为 effective plan。
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
        remaining_ids = {task.id for task in proposed.tasks}
        invalid_live_endpoints = sorted(
            {
                task_id
                for dependency in proposed.live_dependencies
                for task_id in (
                    dependency.producer_task_id,
                    dependency.target_task_id,
                )
                if task_id not in remaining_ids
            }
        )
        if invalid_live_endpoints:
            raise ValueError(
                "replan LIVE dependencies may only connect current-generation "
                "remaining tasks: " + ", ".join(invalid_live_endpoints)
            )
        # endregion 2. 有界提案

        # region 3. 新有效计划：冻结根验收标准，并记录新 digest 后返回
        # 根验收标准保持冻结；重规划只能替换尚未完成的任务。
        bounded = PlanningDecision(
            mode="fanout",
            reason=proposed.reason,
            global_acceptance_criteria=effective_plan.global_acceptance_criteria,
            tasks=proposed.tasks,
            live_dependencies=proposed.live_dependencies,
        )
        new_plan = bounded.to_fanout_plan(
            self.plan.goal, completed_tasks=completed_tasks
        )
        self._record_replan_success(
            effective_plan=new_plan.to_dict(),
            effective_plan_digest=new_plan.digest,
        )
        return new_plan
        # endregion 3. 新有效计划

    def _restore_previous(
        self,
        base_revision: str,
    ) -> tuple[FanoutPlan, list[LiveSubagentResult], list[LiveSubagentResult], int]:
        """校验并恢复有效计划与已合并前缀，不重新调用 Planner 或改写历史结果。"""

        # region 1. Run 身份：initial/effective plan、base revision 与 replan round 必须一致
        # Resume 信任的是 digest 与 base revision，不信任 latest 指针或调用方描述。
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
        # endregion 1. Run 身份

        # region 2. 已合并前缀：逐项验证状态、文件存在性与 candidate SHA
        # 只有 checkpoint 明确列入 merged_task_ids 的 completed 结果可以被恢复。
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
        # endregion 2. 已合并前缀

        # region 3. 隔离重放：先在临时 worktree 合成，再经真实 workspace apply gate
        # validate_recovery_diffs 证明前缀内部可重放；workspace gate 再保护当前目标树。
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
        # endregion 3. 隔离重放

        # region 4. Evidence 历史：只恢复已合并任务的历史 Attempts
        attempt_rows = payload.get("attempt_results") or payload.get("results") or []
        merged_id_set = set(merged_ids)
        restored_attempts = [
            _result_from_payload(row, resumed=True)
            for row in attempt_rows
            if isinstance(row, dict) and str(row.get("task_id")) in merged_id_set
        ]
        return effective_plan, restored, restored_attempts, replan_round
        # endregion 4. Evidence 历史

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
        """原子持久化当前有效计划、全部尝试和已合并前缀，供确定性恢复使用。"""

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
    # endregion 4. 有界恢复与 checkpoint 结束

    # region 5. Trace 证据：集中记录调度、retry、replan 和终态，不参与业务决策
    def _record_fanout_started(self, effective_plan: FanoutPlan) -> None:
        self.events.add(
            0,
            "FanoutCoordinator",
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
            "FanoutCoordinator",
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
            "FanoutCoordinator",
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
            "FanoutCoordinator",
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
            "FanoutCoordinator",
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
            "FanoutCoordinator",
            "replan_result",
            effective_plan=effective_plan,
            effective_plan_digest=effective_plan_digest,
        )

    def _record_replan_failure(self, *, step: int, error: str) -> None:
        self.events.add(
            step,
            "FanoutCoordinator",
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
            "FanoutCoordinator",
            "fanout_done",
            success=fanout_status == "passed",
            status=fanout_status,
            metrics=metrics,
            replan_round=replan_round,
        )
    # endregion 5. Trace 证据结束


# region 6. 纯结果投影：把持久化 payload 和运行事实转换为稳定结果类型
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
# endregion 6. 纯结果投影结束

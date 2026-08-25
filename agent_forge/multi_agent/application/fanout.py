"""一张冻结 ``FanoutPlan`` 的唯一执行权威。

``FanoutCoordinator`` 统一拥有就绪调度、Worker Attempt、候选门禁、严格前缀集成、
HARD-only verified resume 与最终发布；HARD 和 LIVE 是同一 Scheduler 内的两种依赖语义。
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any

from agent_forge.runtime.config import RuntimeConfig

from ..domain.fanout import (
    FANOUT_CHECKPOINT_SCHEMA_VERSION,
    FANOUT_SUMMARY_SCHEMA_VERSION,
    FanoutConflict,
    FanoutCheckpoint,
    FanoutPlan,
    FanoutSummary,
    FanoutTaskResult,
    SubagentTask,
    WorkerAttemptResult,
    WorkerHandoff,
    aggregate_fanout_metrics,
    detect_write_scope_conflicts,
    project_worker_handoff,
)
from .dependencies import FanoutDependencies
from .live_handoff import LiveHandoffRuntime


class CandidateDecision(str, Enum):
    DEFERRED = "DEFERRED"
    REJECTED = "REJECTED"
    INTEGRATED = "INTEGRATED"


@dataclass(frozen=True)
class RunningAttempt:
    task: SubagentTask
    attempt: int
    launch_wave_index: int


@dataclass
class FanoutExecutionState:
    """只保存一次 Run 的可变事实，不承担 Service 或 Policy 职责。"""

    task_results: dict[str, FanoutTaskResult] = field(default_factory=dict)
    attempt_results: list[WorkerAttemptResult] = field(default_factory=list)
    attempt_counts: dict[str, int] = field(default_factory=dict)
    merged_task_ids: list[str] = field(default_factory=list)
    launch_waves: list[list[dict[str, int | str]]] = field(default_factory=list)
    conflicts: list[FanoutConflict] = field(default_factory=list)
    pending: set[str] = field(default_factory=set)
    candidates: dict[str, WorkerAttemptResult] = field(default_factory=dict)
    running: dict[Future[WorkerAttemptResult], RunningAttempt] = field(
        default_factory=dict
    )


class FanoutCoordinator:
    """通过确定性、fail-closed 治理执行一张冻结计划。"""

    # region 1. Public Fanout lifecycle（公开 Fanout 生命周期）
    def __init__(
        self,
        *,
        plan: FanoutPlan,
        base_config: RuntimeConfig,
        dependencies: FanoutDependencies,
        max_workers: int = 4,
        resume_from: str | None = None,
    ) -> None:
        self.plan = plan
        self.base_config = base_config
        self.events = dependencies.events
        self.workspace = dependencies.workspace
        self.artifacts = dependencies.artifacts
        self.workers = dependencies.workers
        requested_workers = int(max_workers)
        if plan.live_dependencies and requested_workers < 2:
            raise ValueError("LIVE dependencies require max_workers >= 2")
        if plan.live_dependencies and resume_from:
            raise ValueError("LIVE coordination resume is not supported in V1")
        self.max_workers = max(1, min(requested_workers, 8))
        self.resume_from = resume_from
        self.live_handoff = (
            LiveHandoffRuntime(plan, self.artifacts)
            if plan.live_dependencies
            else None
        )
        self._integration_order = tuple(
            self.plan.integration_order(task.id for task in self.plan.tasks)
        )

    def run(self) -> FanoutSummary:
        """执行一张冻结 Plan，并发布 checkpoint、summary 与最终 Trace。"""

        started_at = time.monotonic()
        base_head = self.workspace.head()
        self._validate_run_preconditions(base_head)
        state = self._prepare_execution_state(base_head)

        self.artifacts.write_plan(self.plan)
        self._record_fanout_start()
        self._checkpoint(state, base_head, "running")

        self._execute_plan(state, base_head)
        self._materialize_untrusted_results(state)

        integrated_diff_path = self.artifacts.write_integrated_diff(
            self.workspace.diff()
        )
        all_trusted = len(state.merged_task_ids) == len(self.plan.tasks)
        finalizer_result = None
        integrated_attempts = self._integrated_attempts(state) if all_trusted else []
        if all_trusted:
            finalizer_result = self.workers.run_finalizer(
                self.plan,
                integrated_attempts,
            )

        final_decision = finalizer_result.decision if finalizer_result else ""
        fanout_status = self._fanout_status(state, all_trusted, final_decision)
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        finalizer_usage = finalizer_result.usage_summary if finalizer_result else {}
        summary = FanoutSummary(
            schema_version=FANOUT_SUMMARY_SCHEMA_VERSION,
            run_id=self.events.run_id,
            goal=self.plan.goal,
            status=fanout_status,
            plan_digest=self.plan.digest,
            base_head=base_head,
            launch_waves=[list(wave) for wave in state.launch_waves],
            task_results=self._ordered_task_results(state),
            attempt_results=self._ordered_attempt_results(state),
            merged_task_ids=list(state.merged_task_ids),
            conflicts=list(state.conflicts),
            wall_time_ms=elapsed_ms,
            metrics=aggregate_fanout_metrics(
                len(self.plan.tasks),
                state.attempt_results,
                elapsed_ms,
                max_workers=self.max_workers,
                finalizer_usage=finalizer_usage,
            ),
            final_decision=final_decision,
            final_answer=finalizer_result.answer if finalizer_result else "",
            finalizer_trace_path=(
                finalizer_result.trace_path if finalizer_result else ""
            ),
            finalizer_usage_path=(
                finalizer_result.usage_path if finalizer_result else ""
            ),
            finalizer_usage_summary=finalizer_usage,
            criterion_results=(
                list(finalizer_result.criterion_results) if finalizer_result else []
            ),
            integrated_diff_path=integrated_diff_path,
            integration_frontier_task_id=self._frontier_task_id(state),
        )
        self._checkpoint(state, base_head, fanout_status)
        self.artifacts.write_summary(summary)
        self._record_fanout_done(fanout_status, summary.metrics)
        return summary

    def _validate_run_preconditions(self, base_head: str) -> None:
        if not base_head:
            raise RuntimeError("Multi-Agent execution requires a git workspace")
        contains_writes = any(task.write_scope for task in self.plan.tasks)
        if contains_writes and not self.base_config.auto_approve_writes:
            raise RuntimeError(
                "Multi-Agent manual write approval is not recoverable across "
                "ephemeral worktrees; use single mode for per-operation approval"
            )
        if contains_writes and self.workspace.status():
            raise RuntimeError("write-capable Multi-Agent requires a clean workspace")
    # endregion 1. Public Fanout lifecycle（公开 Fanout 生命周期）

    # region 2. Execution state（执行状态）
    def _prepare_execution_state(self, base_head: str) -> FanoutExecutionState:
        state = FanoutExecutionState(
            pending={task.id for task in self.plan.tasks},
        )
        if self.resume_from:
            self._restore_hard_prefix(state, base_head)
        state.pending.difference_update(state.merged_task_ids)
        return state

    def _frontier_task_id(self, state: FanoutExecutionState) -> str:
        index = len(state.merged_task_ids)
        return self._integration_order[index] if index < len(self._integration_order) else ""

    def _frontier_terminal(self, state: FanoutExecutionState) -> bool:
        frontier = self._frontier_task_id(state)
        if not frontier:
            return False
        frontier_result = state.task_results.get(frontier)
        return frontier_result is not None and frontier_result.status != "integrated"
    # endregion 2. Execution state（执行状态）

    # region 3. COMMON readiness scheduler（公共就绪调度）
    def _execute_plan(self, state: FanoutExecutionState, base_head: str) -> None:
        """提交就绪 Attempt、收集 Future，并推进唯一严格集成前沿。"""

        task_by_id = {task.id: task for task in self.plan.tasks}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while state.pending or state.running or state.candidates:
                # region 1. 本轮就绪提交与完成收集
                # Scheduler 先提交当前可运行 Attempt，再按稳定顺序收集已完成 Future。
                progress = False
                if not self._frontier_terminal(state):
                    progress |= self._launch_ready_attempts(
                        executor,
                        state,
                        task_by_id,
                    )

                completed = [future for future in state.running if future.done()]
                if completed:
                    self._collect_completed_attempts(state, completed)
                    progress = True
                # endregion 1. 本轮就绪提交与完成收集

                # region 2. 严格前沿推进与持久化
                if self._advance_integration_frontier(state, task_by_id):
                    progress = True

                if progress:
                    self._checkpoint(state, base_head, "running")
                    continue
                # endregion 2. 严格前沿推进与持久化

                # region 3. 无进展时终止或等待
                # 前沿已终止时不再启动新 Attempt，但允许已经运行的 Attempt 留下真实证据。
                if self._frontier_terminal(state) and not state.running:
                    break
                if state.running:
                    self._wait_for_progress(state)
                    continue
                # 没有运行中的 Worker 时，不会再有 Future 或 LIVE 事件解锁依赖图。
                break
                # endregion 3. 无进展时终止或等待

    def _launch_ready_attempts(
        self,
        executor: ThreadPoolExecutor,
        state: FanoutExecutionState,
        task_by_id: dict[str, SubagentTask],
    ) -> bool:
        slots = self.max_workers - len(state.running)
        if slots <= 0:
            return False
        running_tasks = [entry.task for entry in state.running.values()]
        selected: list[tuple[SubagentTask, int]] = []
        for task in self.plan.tasks:
            if len(selected) >= slots or task.id not in state.pending:
                continue
            if task.id in state.task_results or task.id in state.candidates:
                continue
            if not self._hard_dependencies_ready(task, set(state.merged_task_ids)):
                continue
            if not self._live_dependencies_ready(task):
                continue
            if detect_write_scope_conflicts([task, *running_tasks, *(t for t, _ in selected)]):
                continue
            attempt = state.attempt_counts.get(task.id, 0) + 1
            if attempt > 2:
                raise AssertionError("Worker Attempt 3 is forbidden")
            selected.append((task, attempt))

        if not selected:
            return False
        wave_index = len(state.launch_waves) + 1
        wave: list[dict[str, int | str]] = [
            {"task_id": task.id, "attempt": attempt}
            for task, attempt in selected
        ]
        base_diff = self.workspace.diff()
        for task, attempt in selected:
            state.attempt_counts[task.id] = attempt
            state.pending.remove(task.id)
            coordination = self._prepare_live_worker_context(task, attempt)
            future = executor.submit(
                self._run_worker_attempt,
                task,
                attempt,
                wave_index,
                base_diff,
                self._dependency_handoffs(task, state),
                coordination,
            )
            state.running[future] = RunningAttempt(task, attempt, wave_index)
        state.launch_waves.append(wave)
        self._record_wave_launched(wave_index, wave)
        return True

    def _wait_for_progress(self, state: FanoutExecutionState) -> None:
        revision = self.live_handoff.state_revision if self.live_handoff else 0
        wait(tuple(state.running), timeout=0.25, return_when=FIRST_COMPLETED)
        if self.live_handoff and not any(future.done() for future in state.running):
            self.live_handoff.wait_for_change(revision, timeout=0.25)

    def _collect_completed_attempts(
        self,
        state: FanoutExecutionState,
        completed: list[Future[WorkerAttemptResult]],
    ) -> None:
        position = {task_id: index for index, task_id in enumerate(self._integration_order)}
        completed.sort(key=lambda future: position[state.running[future].task.id])
        for future in completed:
            running = state.running.pop(future)
            attempt_result = future.result()
            state.attempt_results.append(attempt_result)
            self._record_attempt_finished(attempt_result)

            if attempt_result.status == "candidate_produced":
                state.candidates[attempt_result.task_id] = attempt_result
                # Phase A 立即执行，即使该 Candidate 尚未位于当前 Frontier。
                self._integrate_candidate(running.task, attempt_result, state)
                continue

            if (
                not self._frontier_terminal(state)
                and self._worker_retry_allowed(attempt_result)
            ):
                state.pending.add(attempt_result.task_id)
                self._record_worker_retry(attempt_result)
                continue

            # 严格 Frontier 失败后，后续 Task 即使可重试也不再启动新 Attempt。
            if (
                self._frontier_terminal(state)
                and attempt_result.status == "retryable_failure"
            ):
                continue
            state.task_results[attempt_result.task_id] = FanoutTaskResult(
                task_id=attempt_result.task_id,
                status="failed",
                failure_kind=(
                    attempt_result.failure_kind or "worker_execution_failed"
                ),
                final_attempt=attempt_result.attempt,
                handoff=attempt_result.handoff,
                error=attempt_result.error,
                unresolved_issues=tuple(attempt_result.unresolved_issues),
            )

    def _advance_integration_frontier(
        self,
        state: FanoutExecutionState,
        task_by_id: dict[str, SubagentTask],
    ) -> bool:
        advanced = False
        while True:
            frontier = self._frontier_task_id(state)
            if not frontier or self._frontier_terminal(state):
                return advanced
            candidate = state.candidates.get(frontier)
            if candidate is None:
                return advanced
            decision = self._integrate_candidate(
                task_by_id[frontier],
                candidate,
                state,
            )
            if decision == CandidateDecision.INTEGRATED:
                state.candidates.pop(frontier, None)
                advanced = True
                continue
            return advanced
    # endregion 3. COMMON readiness scheduler（公共就绪调度）

    # region 4. HARD dependency rules（严格依赖规则）
    @staticmethod
    def _hard_dependencies_ready(
        task: SubagentTask,
        integrated_task_ids: set[str],
    ) -> bool:
        return set(task.depends_on).issubset(integrated_task_ids)

    def _dependency_handoffs(
        self,
        task: SubagentTask,
        state: FanoutExecutionState,
    ) -> list[WorkerHandoff]:
        handoffs: list[WorkerHandoff] = []
        for dependency in task.depends_on:
            dependency_result = state.task_results.get(dependency)
            if (
                dependency_result
                and dependency_result.status == "integrated"
                and dependency_result.handoff
            ):
                handoffs.append(dependency_result.handoff)
        return handoffs
    # endregion 4. HARD dependency rules（严格依赖规则）

    # region 5. LIVE coordination rules（实时协作规则）
    def _live_dependencies_ready(self, task: SubagentTask) -> bool:
        inbound = self.plan.live_dependencies_for(task.id)
        if not inbound:
            return True
        if self.live_handoff is None:  # pragma: no cover - plan construction guarantees it
            return False
        return self.live_handoff.live_ready(task.id)

    def _prepare_live_worker_context(self, task: SubagentTask, attempt: int) -> Any:
        # LiveHandoffRuntime 只管理所有 LIVE edge 两端 Task 的并集。
        if self.live_handoff is None or task.id not in self.plan.live_task_ids:
            return None
        return self.live_handoff.begin_attempt(task.id, attempt)

    def _live_producers_integrated(
        self,
        task: SubagentTask,
        integrated_task_ids: set[str],
    ) -> bool:
        producers = {
            dependency.producer_task_id
            for dependency in self.plan.live_dependencies_for(task.id)
        }
        return producers.issubset(integrated_task_ids)

    def _authorize_live_freshness(
        self,
        task: SubagentTask,
        attempt: WorkerAttemptResult,
    ) -> tuple[bool, str]:
        # 只有存在入站 LIVE edge 的 Consumer 才需要最终 freshness 授权。
        if not self.plan.live_dependencies_for(task.id):
            return True, ""
        if self.live_handoff is None:  # pragma: no cover - constructor guarantees it
            return False, "LIVE Runtime is unavailable"
        try:
            self.live_handoff.authorize_integration(task.id, attempt.attempt)
        except RuntimeError as exc:
            return False, str(exc)
        return True, ""
    # endregion 5. LIVE coordination rules（实时协作规则）

    # region 6. COMMON Worker execution（公共 Worker 执行）
    def _run_worker_attempt(
        self,
        task: SubagentTask,
        attempt: int,
        launch_wave_index: int,
        base_diff: str,
        dependency_handoffs: list[WorkerHandoff],
        coordination: Any,
    ) -> WorkerAttemptResult:
        """一次真实 Worker Attempt 生命周期的唯一 Owner。"""

        # region 1. 调用唯一 Worker Port 并规范化异常
        # 无论首轮或重试，都从同一入口获得结构化 WorkerAttemptResult。
        try:
            worker_result = self.workers.run_worker(
                task,
                launch_wave_index,
                base_diff,
                dependency_handoffs,
                attempt,
                coordination,
            )
            if (
                worker_result.task_id != task.id
                or worker_result.attempt != attempt
                or worker_result.launch_wave_index != launch_wave_index
            ):
                worker_result = WorkerAttemptResult(
                    task_id=task.id,
                    attempt=attempt,
                    launch_wave_index=launch_wave_index,
                    status="terminal_failure",
                    failure_kind="worker_result_identity_mismatch",
                    error="Worker result identity does not match submitted Attempt",
                    unresolved_issues=[
                        "Worker result identity does not match submitted Attempt"
                    ],
                )
        except Exception as exc:
            retryable, failure_kind = self._classify_worker_exception(exc)
            worker_result = WorkerAttemptResult(
                task_id=task.id,
                attempt=attempt,
                launch_wave_index=launch_wave_index,
                status="retryable_failure" if retryable else "terminal_failure",
                failure_kind=failure_kind,
                retryable=retryable,
                error=str(exc),
                unresolved_issues=[str(exc)],
            )
        # endregion 1. 调用唯一 Worker Port 并规范化异常

        # region 2. 投影有界 HARD Handoff
        if worker_result.handoff is None:
            worker_result.handoff = project_worker_handoff(worker_result)
        # endregion 2. 投影有界 HARD Handoff

        # region 3. 关闭 LIVE-only Attempt 状态
        if self.live_handoff is not None and task.id in self.plan.live_task_ids:
            self.live_handoff.finish_attempt(
                task.id,
                attempt,
                success=worker_result.status == "candidate_produced",
            )
        # endregion 3. 关闭 LIVE-only Attempt 状态
        return worker_result

    @staticmethod
    def _classify_worker_exception(exc: Exception) -> tuple[bool, str]:
        if isinstance(exc, TimeoutError):
            return True, "provider_timeout"
        if isinstance(exc, ConnectionError):
            return True, "provider_connection_failure"
        return False, "worker_port_exception"

    @staticmethod
    def _worker_retry_allowed(result: WorkerAttemptResult) -> bool:
        fail_closed_markers = (
            "scope",
            "no_patch",
            "merge",
            "stale",
            "policy",
            "approval",
            "permission",
            "guardrail",
            "blocked",
        )
        return (
            result.attempt == 1
            and result.status == "retryable_failure"
            and result.retryable
            and not any(marker in result.failure_kind.lower() for marker in fail_closed_markers)
        )
    # endregion 6. COMMON Worker execution（公共 Worker 执行）

    # region 7. COMMON candidate integration（公共候选集成）
    def _integrate_candidate(
        self,
        task: SubagentTask,
        attempt: WorkerAttemptResult,
        state: FanoutExecutionState,
    ) -> CandidateDecision:
        """候选本地校验与可信集成的唯一 Authority。"""

        # region 1. Phase A：与 Frontier 无关的确定性 Candidate 本地校验
        # Candidate 一出现就验证 artifact、实际写域和 patch contract，避免延迟真实失败。
        local_failure = self._candidate_local_failure(task, attempt)
        if local_failure:
            failure_kind, detail = local_failure
            self._record_candidate_gate(
                task.id,
                attempt.attempt,
                failure_kind,
                CandidateDecision.REJECTED,
                detail,
            )
            self._reject_candidate(task, attempt, state, failure_kind, detail)
            state.candidates.pop(task.id, None)
            return CandidateDecision.REJECTED
        self._record_candidate_gate(
            task.id,
            attempt.attempt,
            "candidate_local_validation",
            "ACCEPTED",
        )
        # endregion 1. Phase A：与 Frontier 无关的确定性 Candidate 本地校验

        # region 2. Phase B：可信集成前置授权
        # 只有当前 Frontier 且 HARD/LIVE trust barrier 都满足时才检查最终 freshness。
        if self._frontier_task_id(state) != task.id:
            self._record_candidate_gate(
                task.id,
                attempt.attempt,
                "strict_integration_frontier",
                CandidateDecision.DEFERRED,
            )
            return CandidateDecision.DEFERRED
        integrated_task_ids = set(state.merged_task_ids)
        if not self._hard_dependencies_ready(task, integrated_task_ids):
            self._record_candidate_gate(
                task.id,
                attempt.attempt,
                "hard_integrated_readiness",
                CandidateDecision.DEFERRED,
            )
            return CandidateDecision.DEFERRED
        if not self._live_producers_integrated(task, integrated_task_ids):
            self._record_candidate_gate(
                task.id,
                attempt.attempt,
                "live_producer_integrated",
                CandidateDecision.DEFERRED,
            )
            return CandidateDecision.DEFERRED
        fresh, detail = self._authorize_live_freshness(task, attempt)
        if not fresh:
            self._record_candidate_gate(
                task.id,
                attempt.attempt,
                "live_final_freshness",
                CandidateDecision.REJECTED,
                detail,
            )
            self._reject_candidate(
                task,
                attempt,
                state,
                "stale_live_dependency",
                detail,
            )
            state.candidates.pop(task.id, None)
            return CandidateDecision.REJECTED
        # endregion 2. Phase B：可信集成前置授权

        # region 3. Patch dry-check、apply 与 trusted commit
        # 写 Task 只有在 dry-check 与真实 apply 都成功后才能推进严格 Frontier。
        if task.write_scope:
            candidate = self.artifacts.read_text(attempt.candidate_diff_path)
            applicable, detail = self.workspace.apply_unified_diff(
                candidate,
                check_only=True,
            )
            if not applicable:
                self._record_candidate_gate(
                    task.id,
                    attempt.attempt,
                    "patch_dry_check",
                    CandidateDecision.REJECTED,
                    detail,
                )
                self._reject_candidate(
                    task,
                    attempt,
                    state,
                    "merge_conflict",
                    f"candidate diff apply check failed: {detail}",
                )
                state.candidates.pop(task.id, None)
                return CandidateDecision.REJECTED
            applied, detail = self.workspace.apply_unified_diff(
                candidate,
                check_only=False,
            )
            if not applied:
                self._record_candidate_gate(
                    task.id,
                    attempt.attempt,
                    "patch_apply",
                    CandidateDecision.REJECTED,
                    detail,
                )
                self._reject_candidate(
                    task,
                    attempt,
                    state,
                    "merge_conflict",
                    f"candidate diff apply failed: {detail}",
                )
                state.candidates.pop(task.id, None)
                return CandidateDecision.REJECTED

        handoff = attempt.handoff or project_worker_handoff(attempt)
        state.task_results[task.id] = FanoutTaskResult(
            task_id=task.id,
            status="integrated",
            final_attempt=attempt.attempt,
            handoff=handoff,
        )
        state.merged_task_ids.append(task.id)
        if self.live_handoff is not None and task.id in self.plan.live_task_ids:
            self.live_handoff.seal_integration(task.id, attempt.attempt, success=True)
        self._record_candidate_gate(
            task.id,
            attempt.attempt,
            "trusted_commit",
            CandidateDecision.INTEGRATED,
        )
        # endregion 3. Patch dry-check、apply 与 trusted commit
        return CandidateDecision.INTEGRATED

    def _candidate_local_failure(
        self,
        task: SubagentTask,
        attempt: WorkerAttemptResult,
    ) -> tuple[str, str] | None:
        candidate = ""
        if task.write_scope:
            if not attempt.candidate_diff_path:
                return "no_patch", "write task produced no candidate diff"
            try:
                candidate = self.artifacts.read_text(attempt.candidate_diff_path)
            except (FileNotFoundError, OSError) as exc:
                return "candidate_artifact_invalid", str(exc)
            digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
            if not attempt.candidate_diff_sha256 or digest != attempt.candidate_diff_sha256:
                return (
                    "candidate_artifact_invalid",
                    "candidate diff digest does not match Worker evidence",
                )

        scope_error = self._candidate_scope_error(task, attempt)
        if scope_error:
            return "scope_violation", scope_error
        if task.write_scope and not candidate.strip():
            return "no_patch", "write task produced no candidate diff"
        return None

    @staticmethod
    def _candidate_scope_error(
        task: SubagentTask,
        attempt: WorkerAttemptResult,
    ) -> str:
        if not attempt.touched_files:
            return ""
        if not task.write_scope:
            return f"read-only task modified files: {attempt.touched_files}"
        for path in attempt.touched_files:
            normalized = path.strip("/")
            if not any(
                normalized == scope.strip("/")
                or normalized.startswith(f"{scope.strip('/')}/")
                for scope in task.write_scope
            ):
                return (
                    "actual touched files escaped declared scope: "
                    f"{attempt.touched_files}"
                )
        return ""

    def _reject_candidate(
        self,
        task: SubagentTask,
        attempt: WorkerAttemptResult,
        state: FanoutExecutionState,
        failure_kind: str,
        detail: str,
    ) -> None:
        state.task_results[task.id] = FanoutTaskResult(
            task_id=task.id,
            status="failed",
            failure_kind=failure_kind,
            final_attempt=attempt.attempt,
            handoff=attempt.handoff,
            error=detail,
            unresolved_issues=(detail,),
        )
        if failure_kind == "merge_conflict":
            state.conflicts.append(FanoutConflict((task.id,), detail))
        if self.live_handoff is not None and task.id in self.plan.live_task_ids:
            self.live_handoff.seal_integration(task.id, attempt.attempt, success=False)
    # endregion 7. COMMON candidate integration（公共候选集成）

    # region 8. HARD-only Resume / checkpoint（严格依赖恢复与检查点）
    def _restore_hard_prefix(
        self,
        state: FanoutExecutionState,
        base_head: str,
    ) -> None:
        """从当前 schema Checkpoint 验证并重放 HARD-only strict prefix。"""

        # region 1. 恢复入口身份校验
        # Resume 只延续被外部中断的运行态 Run；终态结果必须通过 New Run 重试。
        if not self.resume_from:
            return
        payload = self.artifacts.load_resume(self.resume_from)
        if payload.get("schema_version") != FANOUT_CHECKPOINT_SCHEMA_VERSION:
            raise RuntimeError("fanout resume checkpoint schema_version is unsupported")
        if payload.get("status") != "running":
            raise RuntimeError(
                "fanout resume checkpoint is not resumable: "
                f"status={payload.get('status')!r}; only running is allowed"
            )
        if payload.get("plan_digest") != self.plan.digest:
            raise RuntimeError("fanout resume plan digest does not match")
        if payload.get("base_head") != base_head:
            raise RuntimeError("fanout resume base commit does not match")
        # endregion 1. 恢复入口身份校验

        # region 2. 严格 merged prefix 与结构化结果解析
        # merged_task_ids 必须等于稳定 integration order 的连续起始片段。
        merged_ids = list(payload.get("merged_task_ids") or [])
        if len(merged_ids) != len(set(merged_ids)):
            raise RuntimeError("fanout resume merged_task_ids contains duplicates")
        expected_prefix = list(self._integration_order[: len(merged_ids)])
        if merged_ids != expected_prefix:
            raise RuntimeError("fanout resume merged_task_ids is not a strict prefix")

        task_rows = payload.get("task_results")
        attempt_rows = payload.get("attempt_results")
        if not isinstance(task_rows, list) or not isinstance(attempt_rows, list):
            raise RuntimeError("fanout resume checkpoint results are missing")
        if any(not isinstance(row, dict) for row in task_rows + attempt_rows):
            raise RuntimeError("fanout resume checkpoint results are malformed")
        task_results = {
            str(row.get("task_id")): _task_result_from_payload(row)
            for row in task_rows
            if isinstance(row, dict)
        }
        attempts = [
            _attempt_result_from_payload(row, resumed=True)
            for row in attempt_rows
            if isinstance(row, dict)
        ]
        if len(task_results) != len(task_rows):
            raise RuntimeError("fanout resume task_results contain duplicate Task IDs")
        attempt_identities = {
            (attempt_result.task_id, attempt_result.attempt)
            for attempt_result in attempts
        }
        if len(attempt_identities) != len(attempts):
            raise RuntimeError("fanout resume attempt_results contain duplicate Attempts")
        task_by_id = {task.id: task for task in self.plan.tasks}
        recovery_diffs: list[tuple[str, str]] = []
        restored_attempts: list[WorkerAttemptResult] = []
        # endregion 2. 严格 merged prefix 与结构化结果解析

        # region 3. 逐 Task 验证 canonical Attempt 与 Candidate provenance
        # 只有 integrated Task 且存在匹配的 candidate_produced Attempt 才能进入恢复前缀。
        for task_id in merged_ids:
            task_result = task_results.get(task_id)
            if task_result is None or task_result.status != "integrated":
                raise RuntimeError(
                    f"fanout resume merged task is not canonically integrated: {task_id}"
                )
            if task_result.final_attempt is None:
                raise RuntimeError(f"fanout resume integrated task has no Attempt: {task_id}")
            if (
                task_result.handoff is None
                or task_result.handoff.task_id != task_id
            ):
                raise RuntimeError(
                    f"fanout resume integrated task has no canonical Handoff: {task_id}"
                )
            attempt = next(
                (
                    candidate_attempt
                    for candidate_attempt in attempts
                    if candidate_attempt.task_id == task_id
                    and candidate_attempt.attempt == task_result.final_attempt
                    and candidate_attempt.status == "candidate_produced"
                ),
                None,
            )
            if attempt is None:
                raise RuntimeError(
                    f"fanout resume has no canonical Attempt for merged task: {task_id}"
                )
            if (
                attempt.handoff is None
                or attempt.handoff.to_dict() != task_result.handoff.to_dict()
            ):
                raise RuntimeError(
                    f"fanout resume Attempt Handoff does not match Task result: {task_id}"
                )
            task = task_by_id[task_id]
            if task.write_scope:
                if not attempt.candidate_diff_path:
                    raise RuntimeError("fanout resume candidate diff is missing")
                try:
                    candidate = self.artifacts.read_text(attempt.candidate_diff_path)
                except FileNotFoundError as exc:
                    raise RuntimeError(
                        f"fanout resume candidate diff is missing: {attempt.candidate_diff_path}"
                    ) from exc
                digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
                if not attempt.candidate_diff_sha256 or digest != attempt.candidate_diff_sha256:
                    raise RuntimeError(
                        f"fanout resume candidate diff digest does not match for {task_id}"
                    )
                recovery_diffs.append((task_id, candidate))
            state.task_results[task_id] = task_result
            state.merged_task_ids.append(task_id)
            restored_attempts.extend(
                restored_attempt
                for restored_attempt in attempts
                if restored_attempt.task_id == task_id
            )
            state.attempt_counts[task_id] = max(
                restored_attempt.attempt
                for restored_attempt in attempts
                if restored_attempt.task_id == task_id
            )
        # endregion 3. 逐 Task 验证 canonical Attempt 与 Candidate provenance

        # region 4. 隔离验证、dry-check 与确定性重放
        # 所有写 Task 的 Diff 先组合验证，再一次性写入真实 integration workspace。
        if recovery_diffs:
            combined = self.workers.validate_recovery_diffs(recovery_diffs)
            applicable, detail = self.workspace.apply_unified_diff(
                combined,
                check_only=True,
            )
            if not applicable:
                raise RuntimeError(f"fanout resume integration check failed: {detail}")
            applied, detail = self.workspace.apply_unified_diff(
                combined,
                check_only=False,
            )
            if not applied:
                raise RuntimeError(f"fanout resume integration failed: {detail}")
        state.attempt_results.extend(restored_attempts)
        state.launch_waves.extend(_launch_waves_from_payload(payload.get("launch_waves")))
        # endregion 4. 隔离验证、dry-check 与确定性重放

    def _checkpoint(
        self,
        state: FanoutExecutionState,
        base_head: str,
        status: str,
    ) -> None:
        self.artifacts.write_checkpoint(
            FanoutCheckpoint(
                plan_digest=self.plan.digest,
                base_head=base_head,
                status=status,
                merged_task_ids=tuple(state.merged_task_ids),
                task_results=tuple(self._ordered_task_results(state)),
                attempt_results=tuple(self._ordered_attempt_results(state)),
                launch_waves=tuple(
                    tuple(dict(attempt) for attempt in wave)
                    for wave in state.launch_waves
                ),
            )
        )
    # endregion 8. HARD-only Resume / checkpoint（严格依赖恢复与检查点）

    # region 9. Trace / Summary projections（Trace 与汇总投影）
    def _record_fanout_start(self) -> None:
        self.events.add(
            0,
            "FanoutCoordinator",
            "fanout_start",
            plan_digest=self.plan.digest,
            plan=self.plan.to_dict(),
        )

    def _record_wave_launched(
        self,
        launch_wave_index: int,
        attempts: list[dict[str, int | str]],
    ) -> None:
        self.events.add(
            launch_wave_index,
            "FanoutCoordinator",
            "fanout_wave_launched",
            launch_wave_index=launch_wave_index,
            attempts=attempts,
        )

    def _record_attempt_finished(self, result: WorkerAttemptResult) -> None:
        self.events.add(
            result.launch_wave_index,
            "FanoutCoordinator",
            "worker_attempt_finished",
            task_id=result.task_id,
            attempt=result.attempt,
            status=result.status,
            failure_kind=result.failure_kind,
            retryable=result.retryable,
        )

    def _record_worker_retry(self, result: WorkerAttemptResult) -> None:
        self.events.add(
            result.launch_wave_index,
            "FanoutCoordinator",
            "worker_retry",
            task_id=result.task_id,
            prior_attempt=result.attempt,
            failure_kind=result.failure_kind,
        )

    def _record_candidate_gate(
        self,
        task_id: str,
        attempt: int,
        gate: str,
        decision: CandidateDecision | str,
        detail: str = "",
    ) -> None:
        self.events.add(
            len(self.plan.tasks),
            "FanoutCoordinator",
            "candidate_gate",
            task_id=task_id,
            attempt=attempt,
            gate=gate,
            decision=(decision.value if isinstance(decision, CandidateDecision) else decision),
            detail=detail,
        )

    def _record_fanout_done(self, status: str, metrics: dict[str, Any]) -> None:
        self.events.add(
            len(self.plan.tasks) + 1,
            "FanoutCoordinator",
            "fanout_done",
            success=status == "passed",
            status=status,
            metrics=metrics,
            plan_digest=self.plan.digest,
        )

    def _ordered_task_results(
        self,
        state: FanoutExecutionState,
    ) -> list[FanoutTaskResult]:
        return [
            state.task_results[task.id]
            for task in self.plan.tasks
            if task.id in state.task_results
        ]

    def _ordered_attempt_results(
        self,
        state: FanoutExecutionState,
    ) -> list[WorkerAttemptResult]:
        position = {task_id: index for index, task_id in enumerate(self._integration_order)}
        return sorted(
            state.attempt_results,
            key=lambda result: (position[result.task_id], result.attempt),
        )

    def _integrated_attempts(
        self,
        state: FanoutExecutionState,
    ) -> list[WorkerAttemptResult]:
        attempts = {
            (attempt_result.task_id, attempt_result.attempt): attempt_result
            for attempt_result in state.attempt_results
        }
        integrated_attempts: list[WorkerAttemptResult] = []
        for task_id in self._integration_order:
            final_attempt = state.task_results[task_id].final_attempt
            if final_attempt is None:  # pragma: no cover - integrated Task invariant
                raise AssertionError("integrated Task must name its final Attempt")
            integrated_attempts.append(attempts[(task_id, final_attempt)])
        return integrated_attempts

    def _materialize_untrusted_results(self, state: FanoutExecutionState) -> None:
        attempts_by_task: dict[str, list[WorkerAttemptResult]] = {}
        for attempt in state.attempt_results:
            attempts_by_task.setdefault(attempt.task_id, []).append(attempt)
        task_by_id = {task.id: task for task in self.plan.tasks}
        # 按验证过的依赖顺序投影，确保 A -> B -> C 的失败闭包不受声明顺序影响。
        for task_id in self._integration_order:
            task = task_by_id[task_id]
            if task.id in state.task_results:
                continue
            attempts = attempts_by_task.get(task.id, [])
            failed_hard = any(
                dependency in state.task_results
                and state.task_results[dependency].status != "integrated"
                for dependency in task.depends_on
            )
            if failed_hard and not attempts:
                state.task_results[task.id] = FanoutTaskResult(
                    task_id=task.id,
                    status="blocked",
                    failure_kind="blocked_dependency",
                    final_attempt=None,
                    error="one or more HARD dependencies did not integrate",
                    unresolved_issues=(
                        "one or more HARD dependencies did not integrate",
                    ),
                )
                continue
            final_attempt = max((attempt.attempt for attempt in attempts), default=None)
            state.task_results[task.id] = FanoutTaskResult(
                task_id=task.id,
                status="not_integrated",
                failure_kind="integration_frontier_blocked",
                final_attempt=final_attempt,
                handoff=attempts[-1].handoff if attempts else None,
                error="strict integration frontier could not advance to this Task",
                unresolved_issues=(
                    "strict integration frontier could not advance to this Task",
                ),
            )

    @staticmethod
    def _fanout_status(
        state: FanoutExecutionState,
        all_trusted: bool,
        finalizer_decision: str,
    ) -> str:
        if not all_trusted:
            if any(
                task_result.failure_kind in {"scope_violation", "merge_conflict"}
                for task_result in state.task_results.values()
            ):
                return "conflict_resolution_required"
            return "partial_failure"
        if finalizer_decision == "PASS":
            return "passed"
        if finalizer_decision == "BLOCKED":
            return "blocked"
        return "needs_revision"
    # endregion 9. Trace / Summary projections（Trace 与汇总投影）


def _task_result_from_payload(payload: dict[str, Any]) -> FanoutTaskResult:
    values = dict(payload)
    handoff = values.get("handoff")
    if isinstance(handoff, dict):
        values["handoff"] = _handoff_from_payload(handoff)
    allowed = {field_info.name for field_info in fields(FanoutTaskResult)}
    return FanoutTaskResult(**{key: value for key, value in values.items() if key in allowed})


def _attempt_result_from_payload(
    payload: dict[str, Any],
    *,
    resumed: bool,
) -> WorkerAttemptResult:
    values = dict(payload)
    handoff = values.get("handoff")
    if isinstance(handoff, dict):
        values["handoff"] = _handoff_from_payload(handoff)
    values["resumed"] = resumed
    allowed = {field_info.name for field_info in fields(WorkerAttemptResult)}
    return WorkerAttemptResult(
        **{key: value for key, value in values.items() if key in allowed}
    )


def _handoff_from_payload(payload: dict[str, Any]) -> WorkerHandoff:
    allowed = {field_info.name for field_info in fields(WorkerHandoff)}
    return WorkerHandoff(**{key: value for key, value in payload.items() if key in allowed})


def _launch_waves_from_payload(value: Any) -> list[list[dict[str, int | str]]]:
    if not isinstance(value, list):
        raise RuntimeError("fanout resume launch_waves is missing")
    waves: list[list[dict[str, int | str]]] = []
    for wave in value:
        if not isinstance(wave, list):
            raise RuntimeError("fanout resume launch_waves is invalid")
        projected: list[dict[str, int | str]] = []
        for attempt in wave:
            if not isinstance(attempt, dict):
                raise RuntimeError("fanout resume launch wave Attempt is invalid")
            projected.append(
                {
                    "task_id": str(attempt.get("task_id") or ""),
                    "attempt": int(attempt.get("attempt") or 0),
                }
            )
        waves.append(projected)
    return waves

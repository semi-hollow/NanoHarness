"""由 Runtime 治理的里程碑依赖与协作 Worker 调度。"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Condition, RLock
from typing import Any

from ..domain.fanout import SubagentTask
from ..domain.live_handoff import (
    DependencyType,
    LiveEventType,
    LiveHandoffEvent,
    LiveHandoffPlan,
    LiveHandoffSummary,
    LiveWorkerCandidate,
    LiveWorkerResult,
)
from ..ports import LiveWorkerContextPort
from .dependencies import LiveHandoffDependencies


class MilestoneRegistry:
    """持有最新已接受里程碑和 consumer 实际消费版本。"""

    def __init__(self, plan: LiveHandoffPlan) -> None:
        self.plan = plan
        self._latest: dict[tuple[str, str, str], LiveHandoffEvent] = {}
        self._consumed: dict[tuple[str, str, str], int] = {}
        self._accepted_event_ids: set[str] = set()

    def validate(self, event: LiveHandoffEvent) -> str:
        """仅当事件允许改变 registry 状态时返回空字符串。"""

        if event.event_id in self._accepted_event_ids:
            return "duplicate_event"
        if event.event_type in {LiveEventType.READY, LiveEventType.UPDATE}:
            identity = (
                event.producer_task_id,
                event.target_task_id,
                event.semantic_key,
            )
            latest = self._latest.get(identity)
            if event.event_type == LiveEventType.READY:
                if latest is not None:
                    return "milestone_already_ready"
                if event.version != 1:
                    return "READY_must_publish_version_1"
            else:
                if latest is None:
                    return "UPDATE_requires_prior_READY"
                if event.version != latest.version + 1:
                    return "UPDATE_version_must_increment_by_one"
        else:
            consumed = self._consumed.get(
                (
                    event.producer_task_id,
                    event.target_task_id,
                    event.semantic_key,
                )
            )
            if consumed != event.version:
                return "FEEDBACK_must_reference_the_publishers_consumed_version"
        return ""

    def commit(self, event: LiveHandoffEvent) -> None:
        """只在 durable append 成功后提交已校验事件。"""

        self._accepted_event_ids.add(event.event_id)
        if event.event_type in {LiveEventType.READY, LiveEventType.UPDATE}:
            self._latest[
                (
                    event.producer_task_id,
                    event.target_task_id,
                    event.semantic_key,
                )
            ] = event

    def record_consumed(self, task_id: str, event: LiveHandoffEvent) -> None:
        """只记录 target Worker 确实从 mailbox 取出的里程碑事件。"""

        if (
            event.event_type in {LiveEventType.READY, LiveEventType.UPDATE}
            and event.target_task_id == task_id
        ):
            self._consumed[(task_id, event.producer_task_id, event.semantic_key)] = (
                event.version
            )

    def has_ready(self, dependency_producer: str, target: str, key: str) -> bool:
        return (dependency_producer, target, key) in self._latest

    def consumed_versions(self, task_id: str) -> dict[str, int]:
        return {
            f"{producer_task_id}:{semantic_key}": version
            for (consumer_task_id, producer_task_id, semantic_key), version in sorted(
                self._consumed.items()
            )
            if consumer_task_id == task_id
        }

    def stale_dependencies(self, task_id: str) -> list[dict[str, Any]]:
        stale: list[dict[str, Any]] = []
        for dependency in self.plan.dependencies_for(task_id):
            if dependency.dependency_type != DependencyType.LIVE:
                continue
            latest = self._latest.get(
                (
                    dependency.producer_task_id,
                    dependency.target_task_id,
                    dependency.semantic_key,
                )
            )
            consumed = self._consumed.get(
                (
                    task_id,
                    dependency.producer_task_id,
                    dependency.semantic_key,
                )
            )
            if latest is None or consumed != latest.version:
                stale.append(
                    {
                        "producer_task_id": dependency.producer_task_id,
                        "target_task_id": task_id,
                        "semantic_key": dependency.semantic_key,
                        "consumed_version": consumed,
                        "latest_version": latest.version if latest else None,
                    }
                )
        return stale


class WorkerMailbox:
    """保存每个 Worker 的已接受事实 FIFO，不参与调度决策。"""

    def __init__(self) -> None:
        self._pending: dict[str, list[LiveHandoffEvent]] = {}

    def enqueue(self, event: LiveHandoffEvent) -> None:
        self._pending.setdefault(event.target_task_id, []).append(event)

    def drain(self, task_id: str) -> list[LiveHandoffEvent]:
        return self._pending.pop(task_id, [])


class LiveHandoffRuntime:
    """校验并持久化 Worker 事实，再投影为 readiness 与 mailbox 状态。"""

    def __init__(
        self,
        *,
        plan: LiveHandoffPlan,
        dependencies: LiveHandoffDependencies,
    ) -> None:
        self.plan = plan
        self.artifacts = dependencies.artifacts
        self.milestones = MilestoneRegistry(plan)
        self.mailboxes = WorkerMailbox()
        self._started_at = time.monotonic()
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._generation = 0
        self._sequence = 0
        self._task_states = {task.id: "pending" for task in plan.tasks}
        self._events: list[LiveHandoffEvent] = []
        self._timeline: list[dict[str, Any]] = []

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def events(self) -> list[LiveHandoffEvent]:
        with self._lock:
            return list(self._events)

    @property
    def timeline(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(record) for record in self._timeline]

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._started_at) * 1_000)

    def record_run_started(self, run_id: str, scenario: str, mode: str) -> None:
        with self._condition:
            self._append_locked(
                "run_started",
                run_id=run_id,
                scenario=scenario,
                mode=mode,
                plan_digest=self.plan.digest,
            )
            self._notify_locked()

    def record_run_completed(self, status: str, integration_passed: bool) -> None:
        with self._condition:
            self._append_locked(
                "run_completed",
                status=status,
                integration_passed=integration_passed,
            )
            self._notify_locked()

    def record_integration_checked(
        self,
        *,
        status: str,
        integration_passed: bool,
        detail: str,
        stale_dependencies: list[dict[str, Any]],
    ) -> None:
        with self._condition:
            self._append_locked(
                "integration_checked",
                status=status,
                integration_passed=integration_passed,
                detail=detail,
                stale_dependencies=stale_dependencies,
            )
            self._notify_locked()

    def mark_worker_started(self, task_id: str) -> int:
        with self._condition:
            if self._task_states.get(task_id) != "pending":
                raise RuntimeError(f"worker {task_id} cannot start twice")
            started_at_ms = self.elapsed_ms()
            self._append_locked(
                "worker_started",
                task_id=task_id,
                started_at_ms=started_at_ms,
            )
            self._task_states[task_id] = "running"
            self._notify_locked()
            return started_at_ms

    def mark_worker_finished(
        self, task_id: str, *, success: bool, error: str = ""
    ) -> int:
        with self._condition:
            if self._task_states.get(task_id) != "running":
                raise RuntimeError(f"worker {task_id} is not running")
            ended_at_ms = self.elapsed_ms()
            status = "completed" if success else "failed"
            self._append_locked(
                "worker_completed",
                task_id=task_id,
                status=status,
                ended_at_ms=ended_at_ms,
                error=error,
            )
            self._task_states[task_id] = status
            self._notify_locked()
            return ended_at_ms

    def mark_worker_blocked(self, task_id: str, reason: str) -> int:
        with self._condition:
            ended_at_ms = self.elapsed_ms()
            self._append_locked(
                "worker_blocked",
                task_id=task_id,
                status="blocked_dependency",
                ended_at_ms=ended_at_ms,
                reason=reason,
            )
            self._task_states[task_id] = "blocked_dependency"
            self._notify_locked()
            return ended_at_ms

    def publish(self, publisher_task_id: str, event: LiveHandoffEvent) -> bool:
        """遇到冒充发布者、非法路由/版本或过期 Feedback 时 fail closed。"""

        with self._condition:
            rejection = self._validate_publish_locked(publisher_task_id, event)
            if not rejection:
                rejection = self.milestones.validate(event)
            if rejection:
                self._append_locked(
                    "handoff_event_rejected",
                    task_id=publisher_task_id,
                    reason=rejection,
                    event=event.to_dict(),
                )
                self._notify_locked()
                return False

            # Durable append 是提交屏障；只有它成功后才允许改变内存状态。
            self._append_locked(
                "handoff_event",
                task_id=publisher_task_id,
                event=event.to_dict(),
            )
            self.milestones.commit(event)
            self.mailboxes.enqueue(event)
            self._events.append(event)
            self._notify_locked()
            return True

    def drain_mailbox(
        self,
        task_id: str,
        *,
        boundary: str,
    ) -> list[LiveHandoffEvent]:
        """只在显式命名的协作安全边界取出已接受消息。"""

        if not boundary.strip():
            raise ValueError("mailbox drain requires a named safe boundary")
        with self._condition:
            if self._task_states.get(task_id) != "running":
                raise RuntimeError("only a running worker may drain its mailbox")
            events = self.mailboxes.drain(task_id)
            self._append_locked(
                "mailbox_drained",
                task_id=task_id,
                boundary=boundary,
                events=[event.to_dict() for event in events],
            )
            for event in events:
                self.milestones.record_consumed(task_id, event)
            self._notify_locked()
            return events

    def record_action(self, task_id: str, action: str, **data: Any) -> None:
        if not action.strip():
            raise ValueError("worker action must not be empty")
        with self._condition:
            if self._task_states.get(task_id) != "running":
                raise RuntimeError("only a running worker may record an action")
            self._append_locked(
                "worker_action",
                task_id=task_id,
                action=action,
                **data,
            )
            self._notify_locked()

    def can_start(self, task_id: str) -> bool:
        with self._lock:
            if self._task_states.get(task_id) != "pending":
                return False
            for dependency in self.plan.dependencies_for(task_id):
                if dependency.dependency_type == DependencyType.HARD:
                    if (
                        self._task_states.get(dependency.producer_task_id)
                        != "completed"
                    ):
                        return False
                elif not self.milestones.has_ready(
                    dependency.producer_task_id,
                    dependency.target_task_id,
                    dependency.semantic_key,
                ):
                    return False
            return True

    def has_failed_dependency(self, task_id: str) -> bool:
        with self._lock:
            failed_states = {"failed", "blocked_dependency"}
            for dependency in self.plan.dependencies_for(task_id):
                producer_state = self._task_states.get(dependency.producer_task_id)
                if producer_state in failed_states:
                    return True
                if (
                    dependency.dependency_type == DependencyType.LIVE
                    and producer_state == "completed"
                    and not self.milestones.has_ready(
                        dependency.producer_task_id,
                        dependency.target_task_id,
                        dependency.semantic_key,
                    )
                ):
                    return True
            return False

    def consumed_versions(self, task_id: str) -> dict[str, int]:
        with self._lock:
            return self.milestones.consumed_versions(task_id)

    def stale_dependencies(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return self.milestones.stale_dependencies(task_id)

    def wait_for_change(self, generation: int, timeout: float) -> int:
        with self._condition:
            if self._generation == generation:
                self._condition.wait(timeout=max(0.0, timeout))
            return self._generation

    def _validate_publish_locked(
        self,
        publisher_task_id: str,
        event: LiveHandoffEvent,
    ) -> str:
        if event.producer_task_id != publisher_task_id:
            return "worker_cannot_impersonate_another_publisher"
        if self._task_states.get(publisher_task_id) != "running":
            return "only_a_running_worker_may_publish"
        if event.event_type in {LiveEventType.READY, LiveEventType.UPDATE}:
            dependency = self.plan.live_dependency(
                producer_task_id=event.producer_task_id,
                target_task_id=event.target_task_id,
                semantic_key=event.semantic_key,
            )
            if dependency is None:
                return "event_does_not_match_a_LIVE_dependency"
            if (
                event.event_type == LiveEventType.READY
                and self._task_states.get(event.target_task_id) != "pending"
            ):
                return "READY_target_is_not_pending"
            return ""

        dependency = self.plan.live_dependency(
            producer_task_id=event.target_task_id,
            target_task_id=event.producer_task_id,
            semantic_key=event.semantic_key,
        )
        if dependency is None:
            return "FEEDBACK_does_not_match_a_LIVE_dependency"
        if self._task_states.get(event.target_task_id) != "running":
            return "FEEDBACK_target_is_not_running"
        return ""

    def _append_locked(self, record_type: str, **data: Any) -> None:
        self._sequence += 1
        record = {
            "schema_version": 1,
            "sequence": self._sequence,
            "elapsed_ms": self.elapsed_ms(),
            "record_type": record_type,
            **data,
        }
        self.artifacts.append_timeline(record)
        self._timeline.append(record)

    def _notify_locked(self) -> None:
        self._generation += 1
        self._condition.notify_all()


class LiveWorkerContext(LiveWorkerContextPort):
    """把单一 Worker 身份绑定到 Runtime，同时不暴露调度器状态。"""

    def __init__(self, task_id: str, runtime: LiveHandoffRuntime) -> None:
        self._task_id = task_id
        self._runtime = runtime

    @property
    def task_id(self) -> str:
        return self._task_id

    def publish(self, event: LiveHandoffEvent) -> bool:
        return self._runtime.publish(self.task_id, event)

    def drain_mailbox(self, *, boundary: str) -> list[LiveHandoffEvent]:
        return self._runtime.drain_mailbox(self.task_id, boundary=boundary)

    def record_action(self, action: str, **data: Any) -> None:
        self._runtime.record_action(self.task_id, action, **data)


class LiveHandoffCoordinator:
    """仅在 Runtime 判定 HARD/LIVE readiness 满足时启动 Worker。"""

    def __init__(
        self,
        *,
        plan: LiveHandoffPlan,
        dependencies: LiveHandoffDependencies,
        scenario: str,
        mode: str,
        max_workers: int = 4,
        timeout_seconds: float = 30.0,
        run_id: str | None = None,
    ) -> None:
        self.plan = plan
        self.dependencies = dependencies
        self.scenario = scenario
        self.mode = mode
        self.max_workers = max(1, min(int(max_workers), 8))
        if (
            any(
                dependency.dependency_type == DependencyType.LIVE
                for dependency in plan.all_dependencies
            )
            and self.max_workers < 2
        ):
            raise ValueError("LIVE dependencies require at least two worker slots")
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.run_id = run_id or str(uuid.uuid4())
        self.runtime = LiveHandoffRuntime(plan=plan, dependencies=dependencies)

    def run(self) -> LiveHandoffSummary:
        """运行协作 Worker，拒绝过期候选，再执行 integration 校验。"""

        self.runtime.record_run_started(self.run_id, self.scenario, self.mode)
        deadline = time.monotonic() + self.timeout_seconds
        pending = list(self.plan.tasks)
        running: dict[Future[LiveWorkerResult], SubagentTask] = {}
        results_by_task_id: dict[str, LiveWorkerResult] = {}
        generation = self.runtime.generation

        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                while pending or running:
                    for future, task in list(running.items()):
                        if not future.done():
                            continue
                        running.pop(future)
                        results_by_task_id[task.id] = future.result()

                    for task in list(pending):
                        if len(running) >= self.max_workers:
                            break
                        if not self.runtime.can_start(task.id):
                            continue
                        pending.remove(task)
                        future = executor.submit(self._run_worker, task)
                        running[future] = task

                    for task in list(pending):
                        if not self.runtime.has_failed_dependency(task.id):
                            continue
                        pending.remove(task)
                        ended_at_ms = self.runtime.mark_worker_blocked(
                            task.id,
                            "one or more dependencies failed",
                        )
                        results_by_task_id[task.id] = LiveWorkerResult(
                            task_id=task.id,
                            status="blocked_dependency",
                            started_at_ms=ended_at_ms,
                            ended_at_ms=ended_at_ms,
                            error="one or more dependencies failed",
                        )

                    if not pending and not running:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            "live handoff coordinator exceeded its deadline"
                        )
                    if not running and not any(
                        self.runtime.can_start(task.id) for task in pending
                    ):
                        raise RuntimeError("live handoff scheduler made no progress")
                    generation = self.runtime.wait_for_change(
                        generation,
                        min(0.05, remaining),
                    )

            ordered_results = [results_by_task_id[task.id] for task in self.plan.tasks]
            stale_dependencies = [
                stale
                for result in ordered_results
                for stale in self.runtime.stale_dependencies(result.task_id)
            ]
            candidates = {
                result.task_id: result.candidate
                for result in ordered_results
                if result.candidate is not None
            }
            all_workers_completed = all(
                result.status == "completed" for result in ordered_results
            )
            integration_passed = False
            integration_detail = "integration not run"
            if stale_dependencies:
                status = "stale_dependency"
                integration_detail = "candidate consumed an older milestone version"
            elif not all_workers_completed:
                status = "partial_failure"
                integration_detail = "one or more workers did not complete"
            else:
                integration_passed, integration_detail = (
                    self.dependencies.integration.validate(candidates)
                )
                status = "passed" if integration_passed else "failed"

            self.runtime.record_integration_checked(
                status=status,
                integration_passed=integration_passed,
                detail=integration_detail,
                stale_dependencies=stale_dependencies,
            )
            self.runtime.record_run_completed(status, integration_passed)
            wall_time_ms = self.runtime.elapsed_ms()
            summary = LiveHandoffSummary(
                run_id=self.run_id,
                scenario=self.scenario,
                mode=self.mode,
                status=status,
                plan_digest=self.plan.digest,
                wall_time_ms=wall_time_ms,
                results=ordered_results,
                handoff_events=self.runtime.events,
                timeline=self.runtime.timeline,
                stale_dependencies=stale_dependencies,
                integration_passed=integration_passed,
                integration_detail=integration_detail,
                metrics=_summary_metrics(
                    ordered_results,
                    self.runtime.events,
                    wall_time_ms,
                ),
            )
            self.dependencies.artifacts.write_summary(summary)
            return summary
        finally:
            self.dependencies.artifacts.close()

    def _run_worker(self, task: SubagentTask) -> LiveWorkerResult:
        started_at_ms = self.runtime.mark_worker_started(task.id)
        candidate: LiveWorkerCandidate | None = None
        error = ""
        try:
            candidate = self.dependencies.workers.run_worker(
                task,
                LiveWorkerContext(task.id, self.runtime),
            )
            success = candidate.test_passed
            if not success:
                error = "worker candidate validation failed"
        except Exception as exc:
            success = False
            error = f"{type(exc).__name__}: {exc}"
        ended_at_ms = self.runtime.mark_worker_finished(
            task.id,
            success=success,
            error=error,
        )
        return LiveWorkerResult(
            task_id=task.id,
            status="completed" if success else "failed",
            started_at_ms=started_at_ms,
            ended_at_ms=ended_at_ms,
            candidate=candidate,
            consumed_versions=self.runtime.consumed_versions(task.id),
            error=error,
        )


def _summary_metrics(
    results: list[LiveWorkerResult],
    events: list[LiveHandoffEvent],
    wall_time_ms: int,
) -> dict[str, Any]:
    candidates = [result.candidate for result in results if result.candidate]
    return {
        "worker_count": len(results),
        "completed_count": sum(result.status == "completed" for result in results),
        "wall_time_ms": wall_time_ms,
        "summed_worker_duration_ms": sum(result.duration_ms for result in results),
        "handoff_event_count": len(events),
        "ready_count": sum(event.event_type == LiveEventType.READY for event in events),
        "feedback_count": sum(
            event.event_type == LiveEventType.FEEDBACK for event in events
        ),
        "update_count": sum(
            event.event_type == LiveEventType.UPDATE for event in events
        ),
        "retry_count": sum(candidate.retry_count for candidate in candidates),
        "rework_count": sum(candidate.rework_count for candidate in candidates),
        "trajectory_change_count": sum(
            candidate.trajectory_changed for candidate in candidates
        ),
    }

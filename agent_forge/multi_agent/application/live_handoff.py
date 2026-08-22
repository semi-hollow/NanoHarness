"""LIVE facts 的唯一授权、mailbox、版本与 freshness owner。"""

from __future__ import annotations

import time
from threading import Condition, RLock
from typing import Any

from ..domain.live import FanoutPlan
from ..domain.live_handoff import LiveEventType, LiveHandoffEvent
from ..ports import FanoutArtifactPort, LiveWorkerContextPort


class LiveHandoffRuntime:
    """用一个锁维护 LIVE 跨字段不变量；它不调度 Worker，也不应用 Diff。"""

    def __init__(self, plan: FanoutPlan, artifacts: FanoutArtifactPort) -> None:
        self.plan = plan
        self.artifacts = artifacts
        self._condition = Condition(RLock())
        self._generation_number = 1
        self._plan_generation_id = self._generation_identity(plan)
        self._state_revision = 0
        self._sequence = 0
        self._attempts: dict[str, int] = {}
        self._worker_states: dict[str, str] = {
            task.id: "pending" for task in plan.tasks
        }
        self._sealed_attempts: dict[str, int] = {}
        self._events: dict[str, LiveHandoffEvent] = {}
        self._latest: dict[tuple[str, str, str], LiveHandoffEvent] = {}
        self._mailboxes: dict[str, list[LiveHandoffEvent]] = {}
        self._consumed: dict[
            tuple[str, str, str], tuple[int, str, int]
        ] = {}
        self._delivered: dict[str, set[str]] = {}
        self._timeline: list[dict[str, Any]] = []
        self._started_at = time.monotonic()

    @property
    def plan_generation_id(self) -> str:
        with self._condition:
            return self._plan_generation_id

    @property
    def state_revision(self) -> int:
        with self._condition:
            return self._state_revision

    @property
    def timeline(self) -> list[dict[str, Any]]:
        with self._condition:
            return [dict(record) for record in self._timeline]

    def begin_attempt(self, task_id: str, worker_attempt_id: int) -> LiveWorkerContextPort:
        """绑定 Worker attempt；retry 会原子失效旧发布、mailbox 与消费版本。"""

        with self._condition:
            self._require_task(task_id)
            if worker_attempt_id < 1:
                raise ValueError("worker_attempt_id must be positive")
            previous = self._attempts.get(task_id)
            if previous is not None and worker_attempt_id <= previous:
                raise ValueError("worker_attempt_id must increase for retry")
            if previous is not None:
                self._invalidate_attempt_locked(task_id)
            self._attempts[task_id] = worker_attempt_id
            self._worker_states[task_id] = "running"
            self._append_locked(
                "worker_attempt_started",
                task_id=task_id,
                worker_attempt_id=worker_attempt_id,
            )
            self._bump_locked()
        return LiveWorkerContext(task_id, worker_attempt_id, self)

    def finish_attempt(
        self,
        task_id: str,
        worker_attempt_id: int,
        *,
        success: bool,
    ) -> None:
        with self._condition:
            self._require_current_attempt(task_id, worker_attempt_id)
            if self._worker_states.get(task_id) != "running":
                raise RuntimeError(f"worker {task_id} is not running")
            self._worker_states[task_id] = "finished" if success else "failed"
            self._append_locked(
                "worker_attempt_finished",
                task_id=task_id,
                worker_attempt_id=worker_attempt_id,
                success=success,
            )
            self._bump_locked()

    def publish(
        self,
        publisher_task_id: str,
        worker_attempt_id: int,
        *,
        event_type: str,
        target_task_id: str,
        semantic_key: str,
        version: int,
        summary: str,
        evidence: list[str],
        caused_by_event_id: str = "",
    ) -> LiveHandoffEvent:
        """Runtime 注入发布者与代际身份，并在持久化成功后提交内存状态。"""

        try:
            normalized_type = LiveEventType(str(event_type).upper())
        except ValueError as exc:
            raise ValueError("event_type must be READY, FEEDBACK, or UPDATE") from exc
        with self._condition:
            self._require_current_attempt(publisher_task_id, worker_attempt_id)
            if self._worker_states.get(publisher_task_id) != "running":
                raise RuntimeError("only a running Worker may publish coordination")
            event = LiveHandoffEvent(
                event_type=normalized_type,
                publisher_task_id=publisher_task_id,
                target_task_id=target_task_id,
                semantic_key=semantic_key,
                version=version,
                summary=summary,
                evidence=tuple(evidence),
                plan_generation_id=self._plan_generation_id,
                worker_attempt_id=worker_attempt_id,
                caused_by_event_id=caused_by_event_id,
            )
            self._validate_publish_locked(event)
            if event.event_id in self._events:
                raise ValueError("duplicate coordination event")

            # JSONL 追加是提交屏障：持久化失败时不得修改内存状态。
            self._append_locked(
                "handoff_event",
                task_id=publisher_task_id,
                human_authority=False,
                event=event.to_dict(),
            )
            self._events[event.event_id] = event
            if normalized_type in {LiveEventType.READY, LiveEventType.UPDATE}:
                self._latest[self._route_key(event)] = event
            self._mailboxes.setdefault(target_task_id, []).append(event)
            self._bump_locked()
            return event

    def drain_mailbox(
        self,
        task_id: str,
        worker_attempt_id: int,
        *,
        boundary: str,
    ) -> list[LiveHandoffEvent]:
        """只在命名的模型安全边界交付当前 generation/attempt 的事实。"""

        if not boundary.strip():
            raise ValueError("mailbox drain requires a named safe boundary")
        with self._condition:
            self._require_current_attempt(task_id, worker_attempt_id)
            if self._worker_states.get(task_id) != "running":
                raise RuntimeError("only a running Worker may drain coordination")
            pending = self._mailboxes.pop(task_id, [])
            events = [event for event in pending if self._event_is_current_locked(event)]
            if not events:
                return []
            for event in events:
                self._delivered.setdefault(task_id, set()).add(event.event_id)
                if event.event_type in {LiveEventType.READY, LiveEventType.UPDATE}:
                    self._consumed[
                        (task_id, event.publisher_task_id, event.semantic_key)
                    ] = (event.version, event.event_id, event.worker_attempt_id)
            self._append_locked(
                "mailbox_delivered",
                task_id=task_id,
                worker_attempt_id=worker_attempt_id,
                boundary=boundary,
                human_authority=False,
                event_ids=[event.event_id for event in events],
                model_input_revision=self._state_revision + 1,
            )
            self._bump_locked()
            return events

    def live_ready(self, task_id: str) -> bool:
        """返回调度事实：所有 inbound LIVE edge 已有当前 READY/UPDATE。"""

        with self._condition:
            for dependency in self.plan.live_dependencies_for(task_id):
                event = self._latest.get(
                    (
                        dependency.producer_task_id,
                        dependency.target_task_id,
                        dependency.semantic_key,
                    )
                )
                if event is None or not self._event_is_current_locked(event):
                    return False
                if self._worker_states.get(dependency.producer_task_id) == "failed":
                    return False
            return True

    def authorize_integration(self, task_id: str, worker_attempt_id: int) -> None:
        """原子冻结 freshness 判断；成功后该 attempt 不再允许发布新事实。"""

        with self._condition:
            self._require_current_attempt(task_id, worker_attempt_id)
            if self._worker_states.get(task_id) != "finished":
                raise RuntimeError("Worker must finish before integration authorization")
            for dependency in self.plan.live_dependencies_for(task_id):
                producer = dependency.producer_task_id
                producer_attempt = self._attempts.get(producer)
                if self._sealed_attempts.get(producer) != producer_attempt:
                    raise RuntimeError(
                        f"LIVE producer {producer} is not successfully integrated"
                    )
                latest = self._latest.get(
                    (producer, task_id, dependency.semantic_key)
                )
                consumed = self._consumed.get(
                    (task_id, producer, dependency.semantic_key)
                )
                if latest is None or not self._event_is_current_locked(latest):
                    raise RuntimeError(f"LIVE dependency {producer} has no final version")
                if consumed != (
                    latest.version,
                    latest.event_id,
                    latest.worker_attempt_id,
                ):
                    raise RuntimeError(
                        f"Worker {task_id} did not consume final LIVE version "
                        f"{producer}:{dependency.semantic_key}:v{latest.version}"
                    )
            self._worker_states[task_id] = "integration_authorized"
            self._append_locked(
                "integration_authorized",
                task_id=task_id,
                worker_attempt_id=worker_attempt_id,
                consumed_versions=self.consumed_versions(task_id),
            )
            self._bump_locked()

    def seal_integration(
        self,
        task_id: str,
        worker_attempt_id: int,
        *,
        success: bool,
    ) -> None:
        with self._condition:
            self._require_current_attempt(task_id, worker_attempt_id)
            if self._worker_states.get(task_id) not in {
                "finished",
                "integration_authorized",
            }:
                raise RuntimeError("Worker is not ready for integration sealing")
            self._worker_states[task_id] = "integrated" if success else "failed"
            if success:
                self._sealed_attempts[task_id] = worker_attempt_id
            else:
                self._sealed_attempts.pop(task_id, None)
            self._append_locked(
                "integration_sealed",
                task_id=task_id,
                worker_attempt_id=worker_attempt_id,
                success=success,
                final_versions=self.latest_versions_from(task_id),
            )
            self._bump_locked()

    def replace_plan(self, plan: FanoutPlan) -> None:
        """Remaining-plan replan 开启新代际；旧 mailbox/event 状态不跨代复制。"""

        with self._condition:
            self.plan = plan
            self._generation_number += 1
            self._plan_generation_id = self._generation_identity(plan)
            self._attempts.clear()
            self._worker_states = {task.id: "pending" for task in plan.tasks}
            self._sealed_attempts.clear()
            self._events.clear()
            self._latest.clear()
            self._mailboxes.clear()
            self._consumed.clear()
            self._delivered.clear()
            self._append_locked(
                "plan_generation_replaced",
                plan_digest=plan.digest,
            )
            self._bump_locked()

    def wait_for_change(self, state_revision: int, timeout: float) -> int:
        with self._condition:
            if self._state_revision == state_revision:
                self._condition.wait(timeout=max(0.0, timeout))
            return self._state_revision

    def consumed_versions(self, task_id: str) -> dict[str, int]:
        with self._condition:
            return {
                f"{producer}:{semantic_key}": value[0]
                for (consumer, producer, semantic_key), value in sorted(
                    self._consumed.items()
                )
                if consumer == task_id
            }

    def latest_versions_from(self, task_id: str) -> dict[str, int]:
        with self._condition:
            return {
                f"{target}:{semantic_key}": event.version
                for (publisher, target, semantic_key), event in sorted(
                    self._latest.items()
                )
                if publisher == task_id and self._event_is_current_locked(event)
            }

    def _validate_publish_locked(self, event: LiveHandoffEvent) -> None:
        forward = next(
            (
                dependency
                for dependency in self.plan.live_dependencies
                if dependency.producer_task_id == event.publisher_task_id
                and dependency.target_task_id == event.target_task_id
                and dependency.semantic_key == event.semantic_key
            ),
            None,
        )
        reverse = next(
            (
                dependency
                for dependency in self.plan.live_dependencies
                if dependency.producer_task_id == event.target_task_id
                and dependency.target_task_id == event.publisher_task_id
                and dependency.semantic_key == event.semantic_key
            ),
            None,
        )
        if event.event_type in {LiveEventType.READY, LiveEventType.UPDATE}:
            if forward is None:
                raise ValueError("READY/UPDATE route is not authorized by FanoutPlan")
            latest = self._latest.get(self._route_key(event))
            if event.event_type == LiveEventType.READY:
                if event.version != 1 or latest is not None:
                    raise ValueError("READY must publish the first version v1")
            elif latest is None or event.version != latest.version + 1:
                raise ValueError("UPDATE must increment the current version by one")
            if event.event_type == LiveEventType.UPDATE:
                self._validate_update_cause_locked(event)
            return
        if reverse is None:
            raise ValueError("FEEDBACK route is not authorized by FanoutPlan")
        if self._worker_states.get(event.target_task_id) != "running":
            raise ValueError("FEEDBACK target must be a running Worker")
        consumed = self._consumed.get(
            (event.publisher_task_id, event.target_task_id, event.semantic_key)
        )
        if consumed is None or consumed[0] != event.version:
            raise ValueError("FEEDBACK must reference a version consumed by its publisher")

    def _validate_update_cause_locked(self, event: LiveHandoffEvent) -> None:
        cause = self._events.get(event.caused_by_event_id)
        if cause is None or cause.event_type != LiveEventType.FEEDBACK:
            raise ValueError("UPDATE requires an accepted FEEDBACK cause")
        if (
            cause.plan_generation_id != self._plan_generation_id
            or cause.target_task_id != event.publisher_task_id
            or cause.publisher_task_id != event.target_task_id
            or cause.semantic_key != event.semantic_key
            or cause.event_id not in self._delivered.get(event.publisher_task_id, set())
            or not self._event_is_current_locked(cause)
        ):
            raise ValueError("UPDATE cause was not validly delivered on this LIVE edge")

    def _invalidate_attempt_locked(self, task_id: str) -> None:
        self._sealed_attempts.pop(task_id, None)
        self._latest = {
            key: event
            for key, event in self._latest.items()
            if event.publisher_task_id != task_id
        }
        self._consumed = {
            key: value
            for key, value in self._consumed.items()
            if key[0] != task_id and key[1] != task_id
        }
        self._mailboxes = {
            target: [
                event
                for event in events
                if event.publisher_task_id != task_id and target != task_id
            ]
            for target, events in self._mailboxes.items()
        }
        self._delivered.pop(task_id, None)
        # 重试后的 Consumer 可重新消费仍有效的上游事实，但不重放旧 FEEDBACK。
        self._mailboxes[task_id] = [
            event
            for event in self._latest.values()
            if event.target_task_id == task_id
        ]
        self._append_locked("worker_attempt_superseded", task_id=task_id)

    def _event_is_current_locked(self, event: LiveHandoffEvent) -> bool:
        return (
            event.plan_generation_id == self._plan_generation_id
            and self._attempts.get(event.publisher_task_id)
            == event.worker_attempt_id
        )

    def _require_task(self, task_id: str) -> None:
        if task_id not in self._worker_states:
            raise ValueError(f"unknown fanout task: {task_id}")

    def _require_current_attempt(self, task_id: str, worker_attempt_id: int) -> None:
        self._require_task(task_id)
        if self._attempts.get(task_id) != worker_attempt_id:
            raise RuntimeError("worker attempt is stale or was not started")

    @staticmethod
    def _route_key(event: LiveHandoffEvent) -> tuple[str, str, str]:
        return (
            event.publisher_task_id,
            event.target_task_id,
            event.semantic_key,
        )

    def _generation_identity(self, plan: FanoutPlan) -> str:
        return f"g{self._generation_number}-{plan.digest[:16]}"

    def _append_locked(self, record_type: str, **data: Any) -> None:
        self._sequence += 1
        record = {
            "schema_version": 1,
            "sequence": self._sequence,
            "elapsed_ms": int((time.monotonic() - self._started_at) * 1_000),
            "record_type": record_type,
            "plan_generation_id": self._plan_generation_id,
            "state_revision": self._state_revision,
            **data,
        }
        self.artifacts.append_coordination(record)
        self._timeline.append(record)

    def _bump_locked(self) -> None:
        self._state_revision += 1
        self._condition.notify_all()


class LiveWorkerContext(LiveWorkerContextPort):
    """只向一个 Worker 暴露绑定后的发布与消费能力。"""

    def __init__(
        self,
        task_id: str,
        worker_attempt_id: int,
        runtime: LiveHandoffRuntime,
    ) -> None:
        self.task_id = task_id
        self.worker_attempt_id = worker_attempt_id
        self._runtime = runtime

    def publish(
        self,
        *,
        event_type: str,
        target_task_id: str,
        semantic_key: str,
        version: int,
        summary: str,
        evidence: list[str],
        caused_by_event_id: str = "",
    ) -> LiveHandoffEvent:
        return self._runtime.publish(
            self.task_id,
            self.worker_attempt_id,
            event_type=event_type,
            target_task_id=target_task_id,
            semantic_key=semantic_key,
            version=version,
            summary=summary,
            evidence=evidence,
            caused_by_event_id=caused_by_event_id,
        )

    def drain_mailbox(self, *, boundary: str) -> list[LiveHandoffEvent]:
        return self._runtime.drain_mailbox(
            self.task_id,
            self.worker_attempt_id,
            boundary=boundary,
        )

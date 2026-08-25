from __future__ import annotations

import hashlib
import threading
import time
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, Callable

from agent_forge.multi_agent.application.dependencies import FanoutDependencies
from agent_forge.multi_agent.application.fanout import FanoutCoordinator
from agent_forge.multi_agent.application.live_handoff import LiveHandoffRuntime
from agent_forge.multi_agent.domain.fanout import SubagentTask
from agent_forge.multi_agent.domain.live import (
    FANOUT_CHECKPOINT_SCHEMA_VERSION,
    FanoutPlan,
    FanoutTaskResult,
    FinalizerResult,
    WorkerAttemptResult,
)
from agent_forge.multi_agent.domain.live_handoff import LiveDependency
from agent_forge.runtime.config import RuntimeConfig


class _Events:
    def __init__(self) -> None:
        self.run_id = "clean-break-test"
        self.events: list[dict[str, Any]] = []

    def add(
        self,
        step: int,
        agent_name: str,
        event_type: str,
        success: bool = True,
        error: str = "",
        **data: Any,
    ) -> None:
        self.events.append(
            {
                "step": step,
                "agent_name": agent_name,
                "event_type": event_type,
                "success": success,
                "error": error,
                **data,
            }
        )


class _Workspace:
    def __init__(self, *, head: str = "base", dirty: str = "") -> None:
        self._head = head
        self._dirty = dirty
        self.applied: list[str] = []

    def head(self) -> str:
        return self._head

    def status(self) -> str:
        return self._dirty

    def diff(self) -> str:
        return "\n".join(self.applied)

    def apply_unified_diff(
        self,
        diff_text: str,
        *,
        check_only: bool,
    ) -> tuple[bool, str]:
        if "CONFLICT" in diff_text:
            return False, "deterministic conflict"
        if not check_only:
            self.applied.append(diff_text)
        return True, "ok"


class _Artifacts:
    def __init__(self) -> None:
        self.text: dict[str, str] = {}
        self.plan: FanoutPlan | None = None
        self.checkpoints: list[Any] = []
        self.summary: Any = None
        self.coordination: list[dict[str, Any]] = []
        self.resume_payload: dict[str, Any] | None = None

    def write_plan(self, plan: FanoutPlan) -> str:
        self.plan = plan
        return "fanout_plan.json"

    def write_checkpoint(self, checkpoint: Any) -> str:
        self.checkpoints.append(checkpoint)
        return "fanout_checkpoint.json"

    def write_integrated_diff(self, diff_text: str) -> str:
        self.text["integrated.diff"] = diff_text
        return "integrated.diff"

    def write_summary(self, summary: Any) -> None:
        self.summary = summary

    def load_resume(self, path: str) -> dict[str, Any]:
        if self.resume_payload is None:
            raise FileNotFoundError(path)
        return self.resume_payload

    def read_text(self, path: str) -> str:
        if path not in self.text:
            raise FileNotFoundError(path)
        return self.text[path]

    def append_coordination(self, record: dict[str, Any]) -> str:
        self.coordination.append(record)
        return "coordination.jsonl"


Behavior = Callable[[SubagentTask, int, Any], WorkerAttemptResult]


class _Workers:
    def __init__(self, artifacts: _Artifacts, behavior: Behavior) -> None:
        self.artifacts = artifacts
        self.behavior = behavior
        self.calls: list[tuple[str, int, int, float]] = []
        self.finished: dict[tuple[str, int], float] = {}
        self.base_diffs: dict[tuple[str, int], str] = {}
        self.handoffs: dict[tuple[str, int], list[Any]] = {}
        self.finalizer_calls = 0
        self.recovery_diffs: list[list[tuple[str, str]]] = []

    def run_worker(
        self,
        task: SubagentTask,
        launch_wave_index: int,
        base_diff_text: str,
        dependency_handoffs: list[Any],
        attempt: int,
        coordination: Any = None,
    ) -> WorkerAttemptResult:
        self.calls.append((task.id, attempt, launch_wave_index, time.monotonic()))
        self.base_diffs[(task.id, attempt)] = base_diff_text
        self.handoffs[(task.id, attempt)] = list(dependency_handoffs)
        result = self.behavior(task, attempt, coordination)
        result.launch_wave_index = launch_wave_index
        self.finished[(task.id, attempt)] = time.monotonic()
        return result

    def run_finalizer(
        self,
        plan: FanoutPlan,
        results: list[WorkerAttemptResult],
    ) -> FinalizerResult:
        self.finalizer_calls += 1
        return FinalizerResult(
            decision="PASS",
            answer="FINAL: PASS",
            trace_path="finalizer/trace.json",
            usage_path="finalizer/usage.json",
            usage_summary={},
        )

    def validate_recovery_diffs(self, diffs: list[tuple[str, str]]) -> str:
        self.recovery_diffs.append(diffs)
        return "\n".join(diff for _, diff in diffs)


def _task(
    task_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    write_scope: tuple[str, ...] = (),
) -> SubagentTask:
    return SubagentTask(
        id=task_id,
        task=f"do {task_id}",
        depends_on=depends_on,
        write_scope=write_scope,
        allowed_tools=(),
        acceptance_criteria=(f"{task_id} accepted",),
    )


def _plan(
    *tasks: SubagentTask,
    live: tuple[LiveDependency, ...] = (),
) -> FanoutPlan:
    return FanoutPlan(
        goal="clean-break test",
        tasks=tasks,
        global_acceptance_criteria=("all trusted",),
        live_dependencies=live,
    )


def _attempt(
    artifacts: _Artifacts,
    task: SubagentTask,
    attempt: int,
    *,
    status: str = "candidate_produced",
    failure_kind: str = "",
    retryable: bool = False,
    touched_files: list[str] | None = None,
    diff: str | None = None,
) -> WorkerAttemptResult:
    path = f"{task.id}-{attempt}.diff"
    if diff is None:
        diff = f"PATCH:{task.id}:{attempt}" if task.write_scope else ""
    artifacts.text[path] = diff
    return WorkerAttemptResult(
        task_id=task.id,
        attempt=attempt,
        launch_wave_index=1,
        status=status,
        failure_kind=failure_kind,
        retryable=retryable,
        final_answer=f"{task.id} attempt {attempt}",
        touched_files=(
            list(touched_files)
            if touched_files is not None
            else list(task.write_scope)
        ),
        candidate_diff_path=path if task.write_scope else "",
        candidate_diff_sha256=(
            hashlib.sha256(diff.encode("utf-8")).hexdigest()
            if task.write_scope
            else ""
        ),
        artifact_path=f"{task.id}-{attempt}.md",
        trace_path=f"{task.id}-{attempt}-trace.json",
        usage_path=f"{task.id}-{attempt}-usage.json",
        environment_manifest_path=f"{task.id}-{attempt}-environment.json",
    )


def _coordinator(
    plan: FanoutPlan,
    artifacts: _Artifacts,
    workers: _Workers,
    *,
    workspace: _Workspace | None = None,
    events: _Events | None = None,
    max_workers: int = 4,
    resume_from: str | None = None,
) -> tuple[FanoutCoordinator, _Workspace, _Events]:
    selected_workspace = workspace or _Workspace()
    selected_events = events or _Events()
    return (
        FanoutCoordinator(
            plan=plan,
            base_config=RuntimeConfig(
                workspace=".",
                auto_approve_writes=True,
            ),
            dependencies=FanoutDependencies(
                events=selected_events,
                workspace=selected_workspace,
                artifacts=artifacts,
                workers=workers,
            ),
            max_workers=max_workers,
            resume_from=resume_from,
        ),
        selected_workspace,
        selected_events,
    )


class FanoutDomainTests(unittest.TestCase):
    def test_fanout_plan_is_deeply_immutable_and_digest_stable(self) -> None:
        mutable_scope = ["src/a.py"]
        task = SubagentTask(id="A", task="a", write_scope=mutable_scope)
        plan = FanoutPlan(goal="g", tasks=[task])
        digest = plan.digest
        mutable_scope.append("src/b.py")

        self.assertIsInstance(plan.tasks, tuple)
        self.assertEqual(plan.tasks[0].write_scope, ("src/a.py",))
        self.assertEqual(plan.digest, digest)
        with self.assertRaises((AttributeError, FrozenInstanceError)):
            plan.tasks[0].write_scope += ("src/c.py",)  # type: ignore[misc]

    def test_attempt_and_task_results_have_disjoint_status_models(self) -> None:
        attempt = WorkerAttemptResult(
            task_id="A", attempt=1, launch_wave_index=1, status="candidate_produced"
        )
        task = FanoutTaskResult(task_id="A", status="integrated", final_attempt=1)
        self.assertNotEqual(attempt.status, task.status)
        with self.assertRaises(ValueError):
            WorkerAttemptResult(
                task_id="A", attempt=1, launch_wave_index=1, status="integrated"
            )
        with self.assertRaises(ValueError):
            FanoutTaskResult(task_id="A", status="candidate_produced")


class FanoutSchedulerAndFrontierTests(unittest.TestCase):
    def test_independent_workers_overlap_but_trusted_integration_order_is_stable(
        self,
    ) -> None:
        artifacts = _Artifacts()
        plan = _plan(
            _task("A", write_scope=("a.py",)),
            _task("B", write_scope=("b.py",)),
        )
        b_started = threading.Event()

        def behavior(task: SubagentTask, attempt: int, _: Any) -> WorkerAttemptResult:
            if task.id == "A":
                self.assertTrue(b_started.wait(1))
                time.sleep(0.04)
            else:
                b_started.set()
            return _attempt(artifacts, task, attempt)

        workers = _Workers(artifacts, behavior)
        summary = _coordinator(plan, artifacts, workers, max_workers=2)[0].run()

        self.assertLess(workers.finished[("B", 1)], workers.finished[("A", 1)])
        self.assertEqual(
            summary.launch_waves[0],
            [
                {"task_id": "A", "attempt": 1},
                {"task_id": "B", "attempt": 1},
            ],
        )
        self.assertEqual(summary.merged_task_ids, ["A", "B"])
        self.assertEqual(summary.status, "passed")
        self.assertEqual(workers.finalizer_calls, 1)
        self.assertEqual(summary.plan_digest, plan.digest)
        self.assertTrue(
            all(checkpoint.plan_digest == plan.digest for checkpoint in artifacts.checkpoints)
        )
        self.assertEqual(artifacts.summary.plan_digest, plan.digest)

    def test_running_write_scope_overlap_serializes_launches(self) -> None:
        artifacts = _Artifacts()
        plan = _plan(
            _task("A", write_scope=("src",)),
            _task("B", write_scope=("src/b.py",)),
        )

        def behavior(task: SubagentTask, attempt: int, _: Any) -> WorkerAttemptResult:
            if task.id == "A":
                time.sleep(0.03)
            return _attempt(artifacts, task, attempt)

        workers = _Workers(artifacts, behavior)
        summary = _coordinator(plan, artifacts, workers, max_workers=2)[0].run()

        self.assertEqual(summary.launch_waves[0], [{"task_id": "A", "attempt": 1}])
        self.assertEqual(summary.launch_waves[1], [{"task_id": "B", "attempt": 1}])
        b_started = next(row[3] for row in workers.calls if row[:2] == ("B", 1))
        self.assertGreaterEqual(b_started, workers.finished[("A", 1)])
        self.assertEqual(summary.merged_task_ids, ["A", "B"])

    def test_hard_consumer_starts_only_after_trusted_integration_and_gets_handoff(
        self,
    ) -> None:
        artifacts = _Artifacts()
        plan = _plan(
            _task("A", write_scope=("a.py",)),
            _task("B", depends_on=("A",)),
        )
        workers = _Workers(
            artifacts,
            lambda task, attempt, _: _attempt(artifacts, task, attempt),
        )

        summary = _coordinator(plan, artifacts, workers, max_workers=2)[0].run()

        self.assertEqual(summary.launch_waves[0], [{"task_id": "A", "attempt": 1}])
        self.assertEqual(summary.launch_waves[1], [{"task_id": "B", "attempt": 1}])
        b_started = next(row[3] for row in workers.calls if row[:2] == ("B", 1))
        self.assertGreaterEqual(b_started, workers.finished[("A", 1)])
        self.assertIn("PATCH:A:1", workers.base_diffs[("B", 1)])
        self.assertEqual(
            [handoff.task_id for handoff in workers.handoffs[("B", 1)]],
            ["A"],
        )
        self.assertEqual(
            workers.handoffs[("B", 1)][0].status,
            "candidate_produced",
        )
        self.assertEqual(summary.merged_task_ids, ["A", "B"])

    def test_blocked_task_has_no_worker_attempt(self) -> None:
        artifacts = _Artifacts()
        plan = _plan(_task("A"), _task("B", depends_on=("A",)))

        def behavior(task: SubagentTask, attempt: int, _: Any) -> WorkerAttemptResult:
            return _attempt(
                artifacts,
                task,
                attempt,
                status="terminal_failure",
                failure_kind="worker_execution_failed",
            )

        workers = _Workers(artifacts, behavior)
        summary = _coordinator(plan, artifacts, workers)[0].run()
        results = {result.task_id: result for result in summary.task_results}
        self.assertEqual(results["B"].status, "blocked")
        self.assertEqual(results["B"].failure_kind, "blocked_dependency")
        self.assertIsNone(results["B"].final_attempt)
        self.assertEqual([row.task_id for row in summary.attempt_results], ["A"])
        self.assertEqual(summary.metrics["task_count"], 2)
        self.assertEqual(summary.metrics["attempt_count"], 1)
        self.assertEqual(workers.finalizer_calls, 0)

    def test_terminal_frontier_stops_launch_and_later_candidate_is_not_integrated(self) -> None:
        artifacts = _Artifacts()
        plan = _plan(_task("A"), _task("B", write_scope=("b.py",)), _task("C"))

        def behavior(task: SubagentTask, attempt: int, _: Any) -> WorkerAttemptResult:
            if task.id == "A":
                return _attempt(
                    artifacts,
                    task,
                    attempt,
                    status="terminal_failure",
                    failure_kind="worker_execution_failed",
                )
            if task.id == "B":
                time.sleep(0.05)
                result = _attempt(artifacts, task, attempt)
                return result
            raise AssertionError("C must not launch after the terminal frontier")

        workers = _Workers(artifacts, behavior)
        started = time.monotonic()
        summary = _coordinator(plan, artifacts, workers, max_workers=2)[0].run()
        self.assertLess(time.monotonic() - started, 2)
        results = {result.task_id: result for result in summary.task_results}
        self.assertEqual(results["A"].status, "failed")
        self.assertEqual(results["B"].status, "not_integrated")
        self.assertEqual(
            results["B"].failure_kind,
            "integration_frontier_blocked",
        )
        self.assertEqual(results["B"].final_attempt, 1)
        self.assertEqual(results["C"].status, "not_integrated")
        self.assertIsNone(results["C"].final_attempt)
        self.assertNotIn("stale_live_dependency", {row.failure_kind for row in results.values()})
        self.assertNotIn("C", [task_id for task_id, *_ in workers.calls])
        self.assertEqual(summary.merged_task_ids, [])

    def test_non_frontier_scope_violation_is_validated_immediately(self) -> None:
        artifacts = _Artifacts()
        release_a = threading.Event()
        plan = _plan(_task("A", write_scope=("a.py",)), _task("B", write_scope=("b.py",)))

        def behavior(task: SubagentTask, attempt: int, _: Any) -> WorkerAttemptResult:
            if task.id == "A":
                release_a.wait(1)
                return _attempt(artifacts, task, attempt)
            result = _attempt(
                artifacts,
                task,
                attempt,
                touched_files=["outside.py"],
            )
            release_a.set()
            return result

        workers = _Workers(artifacts, behavior)
        summary = _coordinator(plan, artifacts, workers, max_workers=2)[0].run()
        results = {result.task_id: result for result in summary.task_results}
        self.assertEqual(results["A"].status, "integrated")
        self.assertEqual(results["B"].failure_kind, "scope_violation")
        self.assertEqual(summary.merged_task_ids, ["A"])

    def test_write_empty_patch_fails_but_read_only_empty_patch_integrates(self) -> None:
        write_artifacts = _Artifacts()
        write_plan = _plan(_task("write", write_scope=("a.py",)))
        write_workers = _Workers(
            write_artifacts,
            lambda task, attempt, _: _attempt(
                write_artifacts, task, attempt, touched_files=[], diff=""
            ),
        )
        write_summary = _coordinator(write_plan, write_artifacts, write_workers)[0].run()
        self.assertEqual(write_summary.task_results[0].failure_kind, "no_patch")

        read_artifacts = _Artifacts()
        read_plan = _plan(_task("read"))
        read_workers = _Workers(
            read_artifacts,
            lambda task, attempt, _: _attempt(read_artifacts, task, attempt),
        )
        read_summary = _coordinator(read_plan, read_artifacts, read_workers)[0].run()
        self.assertEqual(read_summary.task_results[0].status, "integrated")
        self.assertEqual(read_summary.merged_task_ids, ["read"])

    def test_retry_reenters_scheduler_in_a_new_overlapping_launch_wave(self) -> None:
        artifacts = _Artifacts()
        plan = _plan(_task("A", write_scope=("a.py",)), _task("B", write_scope=("b.py",)))

        def behavior(task: SubagentTask, attempt: int, _: Any) -> WorkerAttemptResult:
            if task.id == "A" and attempt == 1:
                return _attempt(
                    artifacts,
                    task,
                    attempt,
                    status="retryable_failure",
                    failure_kind="provider_timeout",
                    retryable=True,
                )
            if task.id == "B":
                time.sleep(0.08)
            return _attempt(artifacts, task, attempt)

        workers = _Workers(artifacts, behavior)
        coordinator, _, events = _coordinator(
            plan, artifacts, workers, max_workers=2
        )
        summary = coordinator.run()
        a2 = next(row for row in workers.calls if row[0:2] == ("A", 2))
        self.assertLess(a2[3], workers.finished[("B", 1)])
        self.assertEqual(summary.launch_waves[0], [
            {"task_id": "A", "attempt": 1},
            {"task_id": "B", "attempt": 1},
        ])
        self.assertEqual(summary.launch_waves[1], [{"task_id": "A", "attempt": 2}])
        self.assertEqual([row.attempt for row in summary.attempt_results if row.task_id == "A"], [1, 2])
        event_types = [event["event_type"] for event in events.events]
        self.assertIn("fanout_wave_launched", event_types)
        self.assertNotIn("fanout_wave" + "_done", event_types)
        self.assertNotIn("fanout_batch" + "_done", event_types)
        self.assertEqual(summary.metrics["attempt_count"], 3)
        self.assertEqual(summary.merged_task_ids, ["A", "B"])

    def test_second_retryable_failure_terminates_without_attempt_three(self) -> None:
        artifacts = _Artifacts()
        plan = _plan(_task("A"))

        def behavior(task: SubagentTask, attempt: int, _: Any) -> WorkerAttemptResult:
            return _attempt(
                artifacts,
                task,
                attempt,
                status="retryable_failure",
                failure_kind="provider_timeout",
                retryable=True,
            )

        workers = _Workers(artifacts, behavior)
        summary = _coordinator(plan, artifacts, workers)[0].run()

        self.assertEqual([row[1] for row in workers.calls], [1, 2])
        self.assertEqual(summary.task_results[0].status, "failed")
        self.assertEqual(summary.task_results[0].final_attempt, 2)
        self.assertEqual(summary.metrics["attempt_count"], 2)
        self.assertEqual(workers.finalizer_calls, 0)

    def test_fail_closed_failure_kinds_never_retry(self) -> None:
        failure_kinds = (
            "scope_violation",
            "no_patch",
            "merge_conflict",
            "stale_live_dependency",
            "policy_denial",
            "approval_denial",
            "permission_denial",
            "guardrail_denial",
            "blocked_dependency",
        )
        for failure_kind in failure_kinds:
            with self.subTest(failure_kind=failure_kind):
                result = WorkerAttemptResult(
                    task_id="A",
                    attempt=1,
                    launch_wave_index=1,
                    status="retryable_failure",
                    failure_kind=failure_kind,
                    retryable=True,
                )
                self.assertFalse(FanoutCoordinator._worker_retry_allowed(result))

    def test_merge_conflict_fails_closed_without_worker_recovery(self) -> None:
        artifacts = _Artifacts()
        plan = _plan(_task("A", write_scope=("a.py",)))
        workers = _Workers(
            artifacts,
            lambda task, attempt, _: _attempt(
                artifacts,
                task,
                attempt,
                diff="CONFLICT",
            ),
        )

        summary = _coordinator(plan, artifacts, workers)[0].run()

        self.assertEqual([(row[0], row[1]) for row in workers.calls], [("A", 1)])
        self.assertEqual(summary.task_results[0].failure_kind, "merge_conflict")
        self.assertEqual(summary.merged_task_ids, [])
        self.assertEqual(len(summary.conflicts), 1)
        self.assertEqual(workers.finalizer_calls, 0)


class FanoutLiveTests(unittest.TestCase):
    def test_live_ready_feedback_update_and_final_freshness(self) -> None:
        artifacts = _Artifacts()
        edge = LiveDependency("producer", "consumer", "schema")
        plan = _plan(
            _task("producer", write_scope=("producer.py",)),
            _task("consumer", write_scope=("consumer.py",)),
            live=(edge,),
        )
        feedback_published = threading.Event()
        update_published = threading.Event()
        update_consumed = threading.Event()
        consumer_started = threading.Event()
        producer_finished_at = 0.0

        def behavior(task: SubagentTask, attempt: int, coordination: Any) -> WorkerAttemptResult:
            nonlocal producer_finished_at
            if task.id == "producer":
                ready = coordination.publish(
                    event_type="READY",
                    target_task_id="consumer",
                    semantic_key="schema",
                    version=1,
                    summary="schema ready",
                    evidence=["v1"],
                )
                self.assertTrue(ready.event_id)
                feedback_published.wait(1)
                feedback = coordination.drain_mailbox(boundary="before-model")
                self.assertEqual([item.event_type.value for item in feedback], ["FEEDBACK"])
                coordination.publish(
                    event_type="UPDATE",
                    target_task_id="consumer",
                    semantic_key="schema",
                    version=2,
                    summary="schema final",
                    evidence=["v2"],
                    caused_by_event_id=feedback[0].event_id,
                )
                update_published.set()
                update_consumed.wait(1)
                producer_finished_at = time.monotonic()
                return _attempt(artifacts, task, attempt)
            consumer_started.set()
            ready_events = coordination.drain_mailbox(boundary="before-model")
            self.assertEqual([item.event_type.value for item in ready_events], ["READY"])
            coordination.publish(
                event_type="FEEDBACK",
                target_task_id="producer",
                semantic_key="schema",
                version=1,
                summary="need compatibility",
                evidence=["legacy"],
                caused_by_event_id=ready_events[0].event_id,
            )
            feedback_published.set()
            update_published.wait(1)
            update = coordination.drain_mailbox(boundary="after-model")
            self.assertEqual([item.event_type.value for item in update], ["UPDATE"])
            update_consumed.set()
            return _attempt(artifacts, task, attempt)

        workers = _Workers(artifacts, behavior)
        coordinator, _, _ = _coordinator(plan, artifacts, workers, max_workers=2)
        summary = coordinator.run()
        consumer_call = next(row for row in workers.calls if row[0] == "consumer")
        self.assertTrue(consumer_started.is_set())
        self.assertLess(consumer_call[3], producer_finished_at)
        self.assertEqual(summary.status, "passed")
        self.assertEqual(summary.merged_task_ids, ["producer", "consumer"])
        self.assertEqual(
            [
                row["event"]["event_type"]
                for row in coordinator.live_handoff.timeline
                if row["record_type"] == "handoff_event"
            ],
            ["READY", "FEEDBACK", "UPDATE"],
        )
        self.assertTrue(
            all(
                row.get("human_authority") is False
                for row in coordinator.live_handoff.timeline
                if row["record_type"] == "handoff_event"
            )
        )

    def test_consumed_old_live_version_fails_freshness_without_retry(self) -> None:
        artifacts = _Artifacts()
        plan = _plan(
            _task("producer"),
            _task("consumer"),
            live=(LiveDependency("producer", "consumer", "schema"),),
        )
        consumer_consumed_ready = threading.Event()
        producer_published_update = threading.Event()

        def behavior(task: SubagentTask, attempt: int, coordination: Any) -> WorkerAttemptResult:
            if task.id == "producer":
                coordination.publish(
                    event_type="READY",
                    target_task_id="consumer",
                    semantic_key="schema",
                    version=1,
                    summary="initial schema",
                    evidence=["v1"],
                )
                self.assertTrue(consumer_consumed_ready.wait(1))
                feedback = coordination.drain_mailbox(boundary="before-model")
                self.assertEqual([event.event_type.value for event in feedback], ["FEEDBACK"])
                coordination.publish(
                    event_type="UPDATE",
                    target_task_id="consumer",
                    semantic_key="schema",
                    version=2,
                    summary="final schema",
                    evidence=["v2"],
                    caused_by_event_id=feedback[0].event_id,
                )
                producer_published_update.set()
                return _attempt(artifacts, task, attempt)
            ready = coordination.drain_mailbox(boundary="before-model")
            self.assertEqual([event.version for event in ready], [1])
            coordination.publish(
                event_type="FEEDBACK",
                target_task_id="producer",
                semantic_key="schema",
                version=1,
                summary="need final schema",
                evidence=["consumer"],
                caused_by_event_id=ready[0].event_id,
            )
            consumer_consumed_ready.set()
            self.assertTrue(producer_published_update.wait(1))
            # 故意不 drain UPDATE v2，最终 freshness 必须 fail closed。
            return _attempt(artifacts, task, attempt)

        workers = _Workers(artifacts, behavior)
        summary = _coordinator(plan, artifacts, workers, max_workers=2)[0].run()
        results = {result.task_id: result for result in summary.task_results}

        self.assertEqual(results["producer"].status, "integrated")
        self.assertEqual(results["consumer"].status, "failed")
        self.assertEqual(
            results["consumer"].failure_kind,
            "stale_live_dependency",
        )
        self.assertEqual(
            [row.attempt for row in summary.attempt_results if row.task_id == "consumer"],
            [1],
        )
        self.assertEqual(summary.merged_task_ids, ["producer"])
        self.assertEqual(workers.finalizer_calls, 0)

    def test_unintegrated_live_producer_defers_consumer_without_false_stale(self) -> None:
        artifacts = _Artifacts()
        plan = _plan(
            _task("producer"),
            _task("consumer"),
            live=(LiveDependency("producer", "consumer", "schema"),),
        )
        consumer_started = threading.Event()

        def behavior(task: SubagentTask, attempt: int, coordination: Any) -> WorkerAttemptResult:
            if task.id == "producer":
                coordination.publish(
                    event_type="READY",
                    target_task_id="consumer",
                    semantic_key="schema",
                    version=1,
                    summary="schema candidate",
                    evidence=["v1"],
                )
                self.assertTrue(consumer_started.wait(1))
                return _attempt(
                    artifacts,
                    task,
                    attempt,
                    touched_files=["illegal.py"],
                )
            consumer_started.set()
            coordination.drain_mailbox(boundary="before-model")
            return _attempt(artifacts, task, attempt)

        workers = _Workers(artifacts, behavior)
        summary = _coordinator(plan, artifacts, workers, max_workers=2)[0].run()
        results = {result.task_id: result for result in summary.task_results}

        self.assertEqual(results["producer"].failure_kind, "scope_violation")
        self.assertEqual(results["consumer"].status, "not_integrated")
        self.assertEqual(
            results["consumer"].failure_kind,
            "integration_frontier_blocked",
        )
        self.assertNotEqual(
            results["consumer"].failure_kind,
            "stale_live_dependency",
        )

    def test_retry_invalidates_old_live_attempt_and_non_live_task_is_rejected(self) -> None:
        artifacts = _Artifacts()
        plan = _plan(
            _task("producer"),
            _task("consumer"),
            _task("ordinary"),
            live=(LiveDependency("producer", "consumer", "schema"),),
        )
        runtime = LiveHandoffRuntime(plan, artifacts)
        producer1 = runtime.begin_attempt("producer", 1)
        producer1.publish(
            event_type="READY",
            target_task_id="consumer",
            semantic_key="schema",
            version=1,
            summary="old",
            evidence=["old"],
        )
        self.assertTrue(runtime.live_ready("consumer"))
        runtime.finish_attempt("producer", 1, success=False)
        runtime.begin_attempt("producer", 2)
        self.assertFalse(runtime.live_ready("consumer"))
        with self.assertRaises(ValueError):
            runtime.begin_attempt("ordinary", 1)

    def test_live_resume_is_fail_closed(self) -> None:
        artifacts = _Artifacts()
        plan = _plan(
            _task("producer"),
            _task("consumer"),
            live=(LiveDependency("producer", "consumer", "schema"),),
        )
        workers = _Workers(
            artifacts,
            lambda task, attempt, _: _attempt(artifacts, task, attempt),
        )
        with self.assertRaisesRegex(ValueError, "resume is not supported"):
            _coordinator(plan, artifacts, workers, resume_from="checkpoint")


class FanoutResumeTests(unittest.TestCase):
    def test_checkpoint_is_only_authority_and_replays_strict_prefix(self) -> None:
        first_artifacts = _Artifacts()
        plan = _plan(
            _task("A", write_scope=("a.py",)),
            _task("B", depends_on=("A",), write_scope=("b.py",)),
        )

        def first_behavior(task: SubagentTask, attempt: int, _: Any) -> WorkerAttemptResult:
            if task.id == "B":
                return _attempt(
                    first_artifacts,
                    task,
                    attempt,
                    status="terminal_failure",
                    failure_kind="worker_execution_failed",
                )
            return _attempt(first_artifacts, task, attempt)

        first_workers = _Workers(first_artifacts, first_behavior)
        first_summary = _coordinator(plan, first_artifacts, first_workers)[0].run()
        self.assertEqual(first_summary.merged_task_ids, ["A"])
        checkpoint = first_artifacts.checkpoints[-1]
        payload = {
            "schema_version": checkpoint.schema_version,
            "plan_digest": checkpoint.plan_digest,
            "base_head": checkpoint.base_head,
            "status": checkpoint.status,
            "merged_task_ids": list(checkpoint.merged_task_ids),
            "task_results": [row.to_dict() for row in checkpoint.task_results],
            "attempt_results": [row.to_dict() for row in checkpoint.attempt_results],
            "launch_waves": [[dict(item) for item in wave] for wave in checkpoint.launch_waves],
            "updated_at": checkpoint.updated_at,
        }

        resumed_artifacts = _Artifacts()
        resumed_artifacts.resume_payload = payload
        resumed_artifacts.text.update(first_artifacts.text)
        resumed_workers = _Workers(
            resumed_artifacts,
            lambda task, attempt, _: _attempt(resumed_artifacts, task, attempt),
        )
        resumed = _coordinator(
            plan,
            resumed_artifacts,
            resumed_workers,
            resume_from="fanout_checkpoint.json",
        )[0].run()
        self.assertEqual(resumed.merged_task_ids, ["A", "B"])
        self.assertEqual(resumed_workers.recovery_diffs[0][0][0], "A")
        self.assertTrue(next(row for row in resumed.attempt_results if row.task_id == "A").resumed)
        self.assertEqual(
            [handoff.task_id for handoff in resumed_workers.handoffs[("B", 1)]],
            ["A"],
        )

        sha_artifacts = _Artifacts()
        sha_artifacts.text.update(first_artifacts.text)
        sha_artifacts.text["A-1.diff"] = "mutated after checkpoint"
        sha_artifacts.resume_payload = payload
        with self.assertRaisesRegex(RuntimeError, "digest does not match"):
            _coordinator(
                plan,
                sha_artifacts,
                _Workers(
                    sha_artifacts,
                    lambda task, attempt, _: _attempt(
                        sha_artifacts,
                        task,
                        attempt,
                    ),
                ),
                resume_from="fanout_checkpoint.json",
            )[0].run()

        # Standalone Attempt evidence cannot be promoted into trusted state.
        invalid_artifacts = _Artifacts()
        invalid_artifacts.text.update(first_artifacts.text)
        invalid_artifacts.resume_payload = {**payload, "task_results": []}
        invalid_workers = _Workers(
            invalid_artifacts,
            lambda task, attempt, _: _attempt(invalid_artifacts, task, attempt),
        )
        with self.assertRaisesRegex(RuntimeError, "canonically integrated"):
            _coordinator(
                plan,
                invalid_artifacts,
                invalid_workers,
                resume_from="fanout_checkpoint.json",
            )[0].run()

        incomplete_artifacts = _Artifacts()
        incomplete_artifacts.text.update(first_artifacts.text)
        incomplete_task_results = [dict(row) for row in payload["task_results"]]
        incomplete_task_results[0]["handoff"] = None
        incomplete_artifacts.resume_payload = {
            **payload,
            "task_results": incomplete_task_results,
        }
        with self.assertRaisesRegex(RuntimeError, "canonical Handoff"):
            _coordinator(
                plan,
                incomplete_artifacts,
                _Workers(
                    incomplete_artifacts,
                    lambda task, attempt, _: _attempt(
                        incomplete_artifacts,
                        task,
                        attempt,
                    ),
                ),
                resume_from="fanout_checkpoint.json",
            )[0].run()

    def test_resume_rejects_wrong_schema_and_non_prefix(self) -> None:
        artifacts = _Artifacts()
        plan = _plan(_task("A"), _task("B"))
        workers = _Workers(
            artifacts,
            lambda task, attempt, _: _attempt(artifacts, task, attempt),
        )
        base = {
            "schema_version": FANOUT_CHECKPOINT_SCHEMA_VERSION,
            "plan_digest": plan.digest,
            "base_head": "base",
            "merged_task_ids": [],
            "task_results": [],
            "attempt_results": [],
            "launch_waves": [],
        }
        artifacts.resume_payload = {**base, "schema_version": 2}
        with self.assertRaisesRegex(RuntimeError, "schema_version"):
            _coordinator(plan, artifacts, workers, resume_from="checkpoint")[0].run()
        artifacts.resume_payload = {**base, "plan_digest": "f" * 64}
        with self.assertRaisesRegex(RuntimeError, "plan digest"):
            _coordinator(plan, artifacts, workers, resume_from="checkpoint")[0].run()
        artifacts.resume_payload = {**base, "base_head": "other"}
        with self.assertRaisesRegex(RuntimeError, "base commit"):
            _coordinator(plan, artifacts, workers, resume_from="checkpoint")[0].run()
        artifacts.resume_payload = {**base, "merged_task_ids": ["A", "A"]}
        with self.assertRaisesRegex(RuntimeError, "duplicates"):
            _coordinator(plan, artifacts, workers, resume_from="checkpoint")[0].run()
        artifacts.resume_payload = {**base, "merged_task_ids": ["B"]}
        with self.assertRaisesRegex(RuntimeError, "strict prefix"):
            _coordinator(plan, artifacts, workers, resume_from="checkpoint")[0].run()


class MultiAgentVocabularyTests(unittest.TestCase):
    def test_current_production_has_no_removed_architecture_vocabulary(self) -> None:
        root = Path(__file__).resolve().parents[1]
        forbidden = (
            "Fanout" + "Replanner",
            "effective" + "_plan",
            "replan" + "_round",
            "replace" + "_plan",
            "plan_generation" + "_id",
            "serialized_conflict" + "_retry",
            "merge_recovery" + "_failed",
            "fanout_batch" + "_done",
            "fanout_wave" + "_done",
            "build_execution" + "_batches",
            "LiveSubagent" + "Result",
            "LiveFanout" + "Summary",
            "LiveFanout" + "Dependencies",
            "LiveFanout" + "BuildRequest",
            "build_live" + "_fanout",
            "LiveFanout" + "Events",
        )
        production_roots = (root / "agent_forge", root / "apps")
        matches: dict[str, list[str]] = {}
        for production_root in production_roots:
            for path in production_root.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for token in forbidden:
                    if token in text:
                        matches.setdefault(token, []).append(str(path.relative_to(root)))
        self.assertEqual(matches, {})


if __name__ == "__main__":
    unittest.main()

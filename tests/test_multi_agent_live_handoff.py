import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path

from agent_forge.multi_agent.adapters.fanout_files import FanoutFileRepository
from agent_forge.multi_agent.application.dependencies import LiveFanoutDependencies
from agent_forge.multi_agent.application.fanout import FanoutCoordinator
from agent_forge.multi_agent.application.live_handoff import LiveHandoffRuntime
from agent_forge.multi_agent.domain.live import (
    CriterionResult,
    FanoutPlan,
    FinalizerResult,
    LiveSubagentResult,
    project_worker_handoff,
)
from agent_forge.observability.api import TraceRecorder
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.run_control import RuntimeCoordinationSignal


def _task(task_id, *, depends_on=None, scope=None):
    return {
        "id": task_id,
        "task": f"implement {task_id}",
        "depends_on": depends_on or [],
        "write_scope": scope or [f"{task_id}.py"],
        "allowed_tools": ["replace_text"],
        "max_steps": 4,
    }


def _live_plan(*, tasks=None, live=None):
    return FanoutPlan.from_mapping(
        {
            "goal": "coordinate two isolated changes",
            "tasks": tasks or [_task("A"), _task("B")],
            "live_dependencies": live
            or [
                {
                    "producer_task_id": "A",
                    "target_task_id": "B",
                    "semantic_key": "timeout_contract",
                }
            ],
        }
    )


class FanoutPlanContractTest(unittest.TestCase):
    def test_hard_only_serialization_and_digest_are_unchanged(self):
        plan = FanoutPlan.from_mapping(
            {"goal": "x", "tasks": [_task("A", scope=["a.py"]) | {"task": "a"}]}
        )
        expected = {
            "goal": "x",
            "tasks": [
                {
                    "id": "A",
                    "task": "a",
                    "depends_on": [],
                    "write_scope": ["a.py"],
                    "allowed_tools": ["replace_text"],
                    "expected_artifact": "task_output",
                    "max_steps": 4,
                }
            ],
        }
        encoded = json.dumps(expected, ensure_ascii=False, sort_keys=True)
        self.assertEqual(plan.to_dict(), expected)
        self.assertEqual(
            plan.digest,
            hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        )
        self.assertNotIn("live_dependencies", plan.to_dict())

    def test_hard_live_graph_rejects_ambiguous_or_deadlocked_plans(self):
        invalid = [
            {
                "tasks": [_task("A", depends_on=["missing"])],
                "live": [],
            },
            {
                "tasks": [_task("A", depends_on=["A"])],
                "live": [],
            },
            {
                "tasks": [_task("A"), _task("B", depends_on=["A", "A"])],
                "live": [],
            },
            {
                "tasks": [_task("A"), _task("B", depends_on=["A"])],
                "live": [
                    {
                        "producer_task_id": "A",
                        "target_task_id": "B",
                        "semantic_key": "k",
                    }
                ],
            },
            {
                "tasks": [_task("A", depends_on=["B"]), _task("B")],
                "live": [
                    {
                        "producer_task_id": "A",
                        "target_task_id": "B",
                        "semantic_key": "k",
                    }
                ],
            },
            {
                "tasks": [_task("A", scope=["same.py"]), _task("B", scope=["same.py"])],
                "live": [
                    {
                        "producer_task_id": "A",
                        "target_task_id": "B",
                        "semantic_key": "k",
                    }
                ],
            },
        ]
        for case in invalid:
            with self.subTest(case=case), self.assertRaises(ValueError):
                FanoutPlan.from_mapping(
                    {
                        "goal": "invalid",
                        "tasks": case["tasks"],
                        "live_dependencies": case["live"],
                    }
                )


class LiveHandoffRuntimeTest(unittest.TestCase):
    def test_runtime_coordination_cannot_claim_human_authority(self):
        with self.assertRaisesRegex(ValueError, "cannot carry human authority"):
            RuntimeCoordinationSignal(
                event_id="event-1",
                content="peer evidence",
                plan_generation_id="plan-1",
                worker_attempt_id=1,
                publisher_task_id="A",
                target_task_id="B",
                event_type="FEEDBACK",
                semantic_key="timeout_contract",
                version=1,
                human_authority=True,
            )

    def test_ready_feedback_update_and_final_freshness_are_runtime_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = LiveHandoffRuntime(
                _live_plan(), FanoutFileRepository(Path(tmp) / "run")
            )
            producer = runtime.begin_attempt("A", 1)
            ready = producer.publish(
                event_type="READY",
                target_task_id="B",
                semantic_key="timeout_contract",
                version=1,
                summary="timeout field shape is ready",
                evidence=["config schema drafted"],
            )
            consumer = runtime.begin_attempt("B", 1)
            self.assertEqual(
                [event.event_id for event in consumer.drain_mailbox(boundary="before_model")],
                [ready.event_id],
            )
            feedback = consumer.publish(
                event_type="FEEDBACK",
                target_task_id="A",
                semantic_key="timeout_contract",
                version=1,
                summary="legacy_timeout must remain accepted",
                evidence=["existing caller uses legacy_timeout"],
            )
            producer.drain_mailbox(boundary="after_model")
            update = producer.publish(
                event_type="UPDATE",
                target_task_id="B",
                semantic_key="timeout_contract",
                version=2,
                summary="compatibility rule added",
                evidence=["legacy_timeout mapped to timeout"],
                caused_by_event_id=feedback.event_id,
            )
            consumer.drain_mailbox(boundary="before_model")
            runtime.finish_attempt("B", 1, success=True)
            with self.assertRaisesRegex(RuntimeError, "not successfully integrated"):
                runtime.authorize_integration("B", 1)
            runtime.finish_attempt("A", 1, success=True)
            runtime.authorize_integration("A", 1)
            runtime.seal_integration("A", 1, success=True)
            runtime.authorize_integration("B", 1)
            runtime.seal_integration("B", 1, success=True)

            self.assertEqual(runtime.latest_versions_from("A"), {"B:timeout_contract": 2})
            self.assertEqual(runtime.consumed_versions("B"), {"A:timeout_contract": 2})
            update_row = next(
                row
                for row in runtime.timeline
                if row.get("event", {}).get("event_id") == update.event_id
            )
            self.assertFalse(update_row["human_authority"])

    def test_identity_route_cause_attempt_and_generation_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = FanoutFileRepository(Path(tmp) / "run")
            runtime = LiveHandoffRuntime(_live_plan(), repository)
            producer = runtime.begin_attempt("A", 1)
            with self.assertRaises(ValueError):
                producer.publish(
                    event_type="READY",
                    target_task_id="missing",
                    semantic_key="timeout_contract",
                    version=1,
                    summary="bad route",
                    evidence=["none"],
                )
            ready = producer.publish(
                event_type="READY",
                target_task_id="B",
                semantic_key="timeout_contract",
                version=1,
                summary="ready",
                evidence=["schema"],
            )
            consumer = runtime.begin_attempt("B", 1)
            consumer.drain_mailbox(boundary="before_model")
            with self.assertRaises(ValueError):
                producer.publish(
                    event_type="UPDATE",
                    target_task_id="B",
                    semantic_key="timeout_contract",
                    version=2,
                    summary="unproven update",
                    evidence=["claim"],
                )
            runtime.finish_attempt("A", 1, success=False)
            producer_retry = runtime.begin_attempt("A", 2)
            with self.assertRaises(RuntimeError):
                producer.publish(
                    event_type="UPDATE",
                    target_task_id="B",
                    semantic_key="timeout_contract",
                    version=2,
                    summary="stale attempt",
                    evidence=[ready.event_id],
                )
            self.assertFalse(runtime.live_ready("B"))
            producer_retry.publish(
                event_type="READY",
                target_task_id="B",
                semantic_key="timeout_contract",
                version=1,
                summary="new attempt",
                evidence=["new schema"],
            )
            runtime.replace_plan(_live_plan())
            with self.assertRaises(RuntimeError):
                producer_retry.publish(
                    event_type="UPDATE",
                    target_task_id="B",
                    semantic_key="timeout_contract",
                    version=2,
                    summary="old generation",
                    evidence=["stale"],
                )


class _Workspace:
    def __init__(self):
        self.applied = []

    def head(self):
        return "base"

    def status(self):
        return ""

    def diff(self):
        return "\n".join(self.applied)

    def apply_unified_diff(self, diff_text, *, check_only):
        if not check_only:
            self.applied.append(diff_text)
        return True, ""


class _CoordinatingWorker:
    def __init__(self, root, *, consume_final_update=True):
        self.root = Path(root)
        self.consume_final_update = consume_final_update
        self.feedback_published = threading.Event()
        self.update_published = threading.Event()
        self.producer_completed = threading.Event()
        self.consumer_started_before_producer_completed = False

    def bind_effective_plan(self, plan):
        self.plan = plan

    def run_worker(self, task, batch_index, base_diff, handoffs, attempt, coordination=None):
        if coordination is None:
            raise AssertionError("LIVE participant must receive bound coordination")
        if task.id == "A":
            coordination.publish(
                event_type="READY",
                target_task_id="B",
                semantic_key="timeout_contract",
                version=1,
                summary="ready",
                evidence=["schema"],
            )
            if not self.feedback_published.wait(5):
                raise AssertionError("feedback was not published")
            feedback = coordination.drain_mailbox(boundary="before_model")[0]
            coordination.publish(
                event_type="UPDATE",
                target_task_id="B",
                semantic_key="timeout_contract",
                version=2,
                summary="updated",
                evidence=["legacy compatibility"],
                caused_by_event_id=feedback.event_id,
            )
            self.update_published.set()
            self.producer_completed.set()
        else:
            self.consumer_started_before_producer_completed = not self.producer_completed.is_set()
            coordination.drain_mailbox(boundary="before_model")
            coordination.publish(
                event_type="FEEDBACK",
                target_task_id="A",
                semantic_key="timeout_contract",
                version=1,
                summary="legacy requirement",
                evidence=["legacy caller"],
            )
            self.feedback_published.set()
            if not self.update_published.wait(5):
                raise AssertionError("update was not published")
            if self.consume_final_update:
                coordination.drain_mailbox(boundary="before_model")
        worker_dir = self.root / task.id
        worker_dir.mkdir(parents=True, exist_ok=True)
        patch = f"PATCH:{task.id}"
        patch_path = worker_dir / "candidate.diff"
        patch_path.write_text(patch, encoding="utf-8")
        result = LiveSubagentResult(
            task_id=task.id,
            status="completed",
            attempt=attempt,
            batch_index=batch_index,
            touched_files=list(task.write_scope),
            candidate_diff_path=str(patch_path),
            candidate_diff_sha256=hashlib.sha256(patch.encode()).hexdigest(),
            final_answer=f"completed {task.id}",
        )
        result.handoff = project_worker_handoff(result)
        return result

    def run_finalizer(self, plan, results):
        return FinalizerResult(
            decision="PASS",
            answer="FINAL: PASS",
            trace_path="trace.json",
            usage_path="usage.json",
            usage_summary={},
            criterion_results=[CriterionResult("integrated", "PASS", "fixture")],
        )

    def validate_recovery_diffs(self, diffs):
        return "\n".join(diff for _, diff in diffs)


class FanoutCoordinatorLiveTest(unittest.TestCase):
    def test_live_start_is_early_but_integration_waits_for_producer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = _live_plan()
            workers = _CoordinatingWorker(root / "workers")
            trace = TraceRecorder(str(root / "trace.json"))
            coordinator = FanoutCoordinator(
                plan=plan,
                base_config=RuntimeConfig(workspace=str(root), max_steps=4),
                dependencies=LiveFanoutDependencies(
                    events=trace,
                    workspace=_Workspace(),
                    artifacts=FanoutFileRepository(root / "run"),
                    workers=workers,
                ),
                max_workers=2,
            )
            summary = coordinator.run()

            self.assertEqual(summary.status, "passed")
            self.assertTrue(workers.consumer_started_before_producer_completed)
            self.assertEqual(summary.merged_task_ids, ["A", "B"])
            records = coordinator.live_handoff.timeline
            sealed = [
                row["task_id"]
                for row in records
                if row["record_type"] == "integration_sealed" and row["success"]
            ]
            self.assertEqual(sealed, ["A", "B"])

    def test_live_requires_two_workers_and_rejects_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dependencies = LiveFanoutDependencies(
                events=TraceRecorder(str(root / "trace.json")),
                workspace=_Workspace(),
                artifacts=FanoutFileRepository(root / "run"),
                workers=_CoordinatingWorker(root / "workers"),
            )
            with self.assertRaisesRegex(ValueError, "max_workers"):
                FanoutCoordinator(
                    plan=_live_plan(),
                    base_config=RuntimeConfig(workspace=str(root)),
                    dependencies=dependencies,
                    max_workers=1,
                )
            with self.assertRaisesRegex(ValueError, "resume is not supported"):
                FanoutCoordinator(
                    plan=_live_plan(),
                    base_config=RuntimeConfig(workspace=str(root)),
                    dependencies=dependencies,
                    max_workers=2,
                    resume_from="checkpoint.json",
                )

    def test_stale_consumer_candidate_is_rejected_without_crashing_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workers = _CoordinatingWorker(
                root / "workers",
                consume_final_update=False,
            )
            coordinator = FanoutCoordinator(
                plan=_live_plan(),
                base_config=RuntimeConfig(workspace=str(root), max_steps=4),
                dependencies=LiveFanoutDependencies(
                    events=TraceRecorder(str(root / "trace.json")),
                    workspace=_Workspace(),
                    artifacts=FanoutFileRepository(root / "run"),
                    workers=workers,
                ),
                max_workers=2,
                allow_replan=False,
            )

            summary = coordinator.run()

            self.assertEqual(summary.status, "partial_failure")
            consumer = next(result for result in summary.results if result.task_id == "B")
            self.assertEqual(consumer.status, "stale_live_dependency")
            self.assertIn("did not consume final LIVE version", consumer.error)


if __name__ == "__main__":
    unittest.main()

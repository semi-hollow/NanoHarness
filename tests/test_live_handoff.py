"""Live Handoff dependency, mailbox, version, and controlled-case tests."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from typing import Any, Mapping

from agent_forge.multi_agent.adapters.live_handoff_files import (
    JsonlLiveHandoffRepository,
)
from agent_forge.multi_agent.adapters.live_agent_worker import (
    PublishHandoffEventTool,
)
from agent_forge.multi_agent.application.dependencies import LiveHandoffDependencies
from agent_forge.multi_agent.application.live_handoff import (
    LiveHandoffCoordinator,
    LiveHandoffRuntime,
)
from agent_forge.multi_agent.domain.fanout import SubagentTask
from agent_forge.multi_agent.domain.live_handoff import (
    DependencyType,
    HandoffSeverity,
    LiveDependency,
    LiveEventType,
    LiveHandoffEvent,
    LiveHandoffPlan,
    LiveHandoffSummary,
    LiveWorkerCandidate,
)
from agent_forge.multi_agent.ports import (
    LiveHandoffArtifactPort,
    LiveHandoffWorkerPort,
    LiveIntegrationPort,
    LiveWorkerContextPort,
)
from scripts.run_live_handoff_experiments import run_suite
from scripts.run_live_agent_handoff_integration import run_real_agent_loop_case
from agent_forge.safety.permission import PermissionDecision, PermissionPolicy


PROJECT_ROOT = Path(__file__).parents[1]
FROZEN_PLAN = (
    PROJECT_ROOT / "benchmarks" / "experiments" / "live-handoff-v1" / "plan.json"
)


class MemoryArtifacts(LiveHandoffArtifactPort):
    def __init__(self) -> None:
        self.timeline: list[dict[str, Any]] = []
        self.summary: dict[str, Any] | None = None

    def append_timeline(self, record: Mapping[str, Any]) -> None:
        self.timeline.append(dict(record))

    def write_summary(self, summary: LiveHandoffSummary) -> str:
        self.summary = summary.to_dict()
        return "summary.json"

    def close(self) -> None:
        return None


class NeverWorker(LiveHandoffWorkerPort):
    def run_worker(
        self,
        task: SubagentTask,
        context: LiveWorkerContextPort,
    ) -> LiveWorkerCandidate:
        raise AssertionError(f"unexpected worker call: {task.id}/{context.task_id}")


class PassingIntegration(LiveIntegrationPort):
    def validate(
        self,
        candidates: Mapping[str, LiveWorkerCandidate],
    ) -> tuple[bool, str]:
        return True, f"validated {len(candidates)} candidates"


class RecordingIntegration(LiveIntegrationPort):
    def __init__(self) -> None:
        self.calls = 0

    def validate(
        self,
        candidates: Mapping[str, LiveWorkerCandidate],
    ) -> tuple[bool, str]:
        self.calls += 1
        return True, "unexpected integration call"


class StaleCandidateWorker(LiveHandoffWorkerPort):
    def __init__(self) -> None:
        self.consumer_consumed_v1 = Event()

    def run_worker(
        self,
        task: SubagentTask,
        context: LiveWorkerContextPort,
    ) -> LiveWorkerCandidate:
        if task.id == "producer":
            self.assert_published(context, _ready())
            if not self.consumer_consumed_v1.wait(timeout=1):
                raise RuntimeError("consumer never consumed version 1")
            update = LiveHandoffEvent(
                event_type=LiveEventType.UPDATE,
                producer_task_id="producer",
                target_task_id="consumer",
                semantic_key="schema",
                version=2,
                summary="Producer published version 2 before integration.",
                evidence=("schema_sha=version-2",),
            )
            self.assert_published(context, update)
            return LiveWorkerCandidate(payload={"version": 2}, test_passed=True)

        context.drain_mailbox(boundary="before_first_model_turn")
        self.consumer_consumed_v1.set()
        time.sleep(0.02)
        return LiveWorkerCandidate(payload={"consumed": 1}, test_passed=True)

    @staticmethod
    def assert_published(
        context: LiveWorkerContextPort,
        event: LiveHandoffEvent,
    ) -> None:
        if not context.publish(event):
            raise RuntimeError(f"Runtime rejected {event.event_type.value}")


def _task(task_id: str) -> SubagentTask:
    return SubagentTask(id=task_id, task=f"Run {task_id}")


def _plan(dependency_type: DependencyType) -> LiveHandoffPlan:
    return LiveHandoffPlan(
        goal="Exercise dependency semantics",
        tasks=(_task("producer"), _task("consumer")),
        dependencies=(
            LiveDependency(
                producer_task_id="producer",
                target_task_id="consumer",
                dependency_type=dependency_type,
                semantic_key="schema" if dependency_type == DependencyType.LIVE else "",
            ),
        ),
    )


def _runtime(
    dependency_type: DependencyType,
) -> tuple[LiveHandoffRuntime, MemoryArtifacts]:
    artifacts = MemoryArtifacts()
    runtime = LiveHandoffRuntime(
        plan=_plan(dependency_type),
        dependencies=LiveHandoffDependencies(
            artifacts=artifacts,
            workers=NeverWorker(),
            integration=PassingIntegration(),
        ),
    )
    return runtime, artifacts


def _ready() -> LiveHandoffEvent:
    return LiveHandoffEvent(
        event_type=LiveEventType.READY,
        producer_task_id="producer",
        target_task_id="consumer",
        semantic_key="schema",
        version=1,
        summary="Schema v1 is ready.",
        evidence=("accepted_keys=timeout",),
    )


class LiveHandoffDomainTest(unittest.TestCase):
    def test_plan_rejects_unknown_tasks_cycles_and_duplicate_edges(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown live handoff task ids"):
            LiveHandoffPlan(
                goal="unknown",
                tasks=(_task("producer"),),
                dependencies=(
                    LiveDependency(
                        producer_task_id="producer",
                        target_task_id="missing",
                        dependency_type=DependencyType.HARD,
                    ),
                ),
            )
        with self.assertRaisesRegex(ValueError, "cyclic dependencies"):
            LiveHandoffPlan(
                goal="cycle",
                tasks=(_task("producer"), _task("consumer")),
                dependencies=(
                    LiveDependency(
                        producer_task_id="producer",
                        target_task_id="consumer",
                        dependency_type=DependencyType.HARD,
                    ),
                    LiveDependency(
                        producer_task_id="consumer",
                        target_task_id="producer",
                        dependency_type=DependencyType.HARD,
                    ),
                ),
            )
        with self.assertRaisesRegex(ValueError, "dependencies must be unique"):
            LiveHandoffPlan(
                goal="duplicate",
                tasks=(_task("producer"), _task("consumer")),
                dependencies=(
                    LiveDependency(
                        producer_task_id="producer",
                        target_task_id="consumer",
                        dependency_type=DependencyType.HARD,
                    ),
                    LiveDependency(
                        producer_task_id="producer",
                        target_task_id="consumer",
                        dependency_type=DependencyType.LIVE,
                        semantic_key="schema",
                    ),
                ),
            )

    def test_event_rejects_non_integer_version_and_invalid_blocking_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive integer"):
            LiveHandoffEvent(
                event_type=LiveEventType.READY,
                producer_task_id="producer",
                target_task_id="consumer",
                semantic_key="schema",
                version=1.5,  # type: ignore[arg-type]
                summary="Invalid version.",
                evidence=("evidence",),
            )
        with self.assertRaisesRegex(ValueError, "only valid for FEEDBACK"):
            LiveHandoffEvent(
                event_type=LiveEventType.READY,
                producer_task_id="producer",
                target_task_id="consumer",
                semantic_key="schema",
                version=1,
                summary="Invalid severity.",
                evidence=("evidence",),
                severity=HandoffSeverity.BLOCKING,
            )


class LiveHandoffRuntimeTest(unittest.TestCase):
    def test_ready_feedback_and_update_cross_named_safe_boundaries(self) -> None:
        runtime, _ = _runtime(DependencyType.LIVE)
        runtime.mark_worker_started("producer")
        self.assertFalse(runtime.can_start("consumer"))
        self.assertTrue(runtime.publish("producer", _ready()))
        self.assertTrue(runtime.can_start("consumer"))

        runtime.mark_worker_started("consumer")
        ready_events = runtime.drain_mailbox(
            "consumer", boundary="before_first_model_turn"
        )
        self.assertEqual([event.version for event in ready_events], [1])
        self.assertEqual(runtime.consumed_versions("consumer"), {"producer:schema": 1})

        feedback = LiveHandoffEvent(
            event_type=LiveEventType.FEEDBACK,
            producer_task_id="consumer",
            target_task_id="producer",
            semantic_key="schema",
            version=1,
            summary="Downstream requires a compatibility alias.",
            evidence=("fixture=config-v1.yaml",),
            severity=HandoffSeverity.BLOCKING,
        )
        self.assertTrue(runtime.publish("consumer", feedback))
        self.assertEqual(
            runtime.drain_mailbox("producer", boundary="after_tool_observation"),
            [feedback],
        )

        update = LiveHandoffEvent(
            event_type=LiveEventType.UPDATE,
            producer_task_id="producer",
            target_task_id="consumer",
            semantic_key="schema",
            version=2,
            summary="Compatibility alias is now implemented.",
            evidence=("accepted_keys=timeout,legacy_timeout",),
        )
        self.assertTrue(runtime.publish("producer", update))
        self.assertEqual(
            runtime.drain_mailbox("consumer", boundary="before_next_model_turn"),
            [update],
        )
        self.assertEqual(runtime.consumed_versions("consumer"), {"producer:schema": 2})
        self.assertEqual(runtime.stale_dependencies("consumer"), [])

    def test_newer_milestone_marks_completed_consumer_candidate_stale(self) -> None:
        runtime, _ = _runtime(DependencyType.LIVE)
        runtime.mark_worker_started("producer")
        self.assertTrue(runtime.publish("producer", _ready()))
        runtime.mark_worker_started("consumer")
        runtime.drain_mailbox("consumer", boundary="before_first_model_turn")
        runtime.mark_worker_finished("consumer", success=True)

        update = LiveHandoffEvent(
            event_type=LiveEventType.UPDATE,
            producer_task_id="producer",
            target_task_id="consumer",
            semantic_key="schema",
            version=2,
            summary="A later producer version exists.",
            evidence=("schema_sha=version-2",),
        )
        self.assertTrue(runtime.publish("producer", update))
        self.assertEqual(
            runtime.stale_dependencies("consumer"),
            [
                {
                    "producer_task_id": "producer",
                    "target_task_id": "consumer",
                    "semantic_key": "schema",
                    "consumed_version": 1,
                    "latest_version": 2,
                }
            ],
        )

    def test_duplicate_and_late_events_fail_closed_and_leave_evidence(self) -> None:
        runtime, artifacts = _runtime(DependencyType.LIVE)
        runtime.mark_worker_started("producer")
        ready = _ready()
        self.assertTrue(runtime.publish("producer", ready))
        self.assertFalse(runtime.publish("producer", ready))

        runtime.mark_worker_started("consumer")
        runtime.drain_mailbox("consumer", boundary="before_first_model_turn")
        runtime.mark_worker_finished("producer", success=True)
        late_feedback = LiveHandoffEvent(
            event_type=LiveEventType.FEEDBACK,
            producer_task_id="consumer",
            target_task_id="producer",
            semantic_key="schema",
            version=1,
            summary="This feedback arrived after producer completion.",
            evidence=("late=true",),
            severity=HandoffSeverity.BLOCKING,
        )
        self.assertFalse(runtime.publish("consumer", late_feedback))
        reasons = [
            record["reason"]
            for record in artifacts.timeline
            if record["record_type"] == "handoff_event_rejected"
        ]
        self.assertEqual(
            reasons,
            ["duplicate_event", "FEEDBACK_target_is_not_running"],
        )

    def test_causal_reference_must_point_to_an_accepted_event(self) -> None:
        runtime, artifacts = _runtime(DependencyType.LIVE)
        runtime.mark_worker_started("producer")
        invalid = LiveHandoffEvent(
            event_type=LiveEventType.READY,
            producer_task_id="producer",
            target_task_id="consumer",
            semantic_key="schema",
            version=1,
            summary="Invalid causal source.",
            evidence=("fixture",),
            caused_by_event_id="a" * 64,
        )

        self.assertFalse(runtime.publish("producer", invalid))
        self.assertEqual(
            artifacts.timeline[-1]["reason"],
            "caused_by_event_not_accepted",
        )

    def test_publish_tool_binds_worker_identity_and_uses_explicit_action(self) -> None:
        runtime, _ = _runtime(DependencyType.LIVE)
        runtime.mark_worker_started("producer")
        from agent_forge.multi_agent.application.live_handoff import LiveWorkerContext

        tool = PublishHandoffEventTool(LiveWorkerContext("producer", runtime))
        observation = tool.execute(
            {
                "event_type": "READY",
                "producer_task_id": "spoofed-worker",
                "target_task_id": "consumer",
                "semantic_key": "schema",
                "version": 1,
                "summary": "Schema is ready.",
                "evidence": ["fixture"],
            }
        )
        permission, _ = PermissionPolicy().decide("coordination_publish")

        self.assertTrue(observation.success)
        self.assertEqual(runtime.events[0].producer_task_id, "producer")
        self.assertEqual(permission, PermissionDecision.ALLOW)

    def test_hard_dependency_never_unblocks_before_completion(self) -> None:
        runtime, _ = _runtime(DependencyType.HARD)
        runtime.mark_worker_started("producer")
        self.assertFalse(runtime.can_start("consumer"))
        runtime.mark_worker_finished("producer", success=True)
        self.assertTrue(runtime.can_start("consumer"))

    def test_live_producer_completion_without_ready_blocks_consumer(self) -> None:
        runtime, _ = _runtime(DependencyType.LIVE)
        runtime.mark_worker_started("producer")
        runtime.mark_worker_finished("producer", success=True)
        self.assertFalse(runtime.can_start("consumer"))
        self.assertTrue(runtime.has_failed_dependency("consumer"))

    def test_coordinator_rejects_stale_candidate_before_integration(self) -> None:
        artifacts = MemoryArtifacts()
        workers = StaleCandidateWorker()
        integration = RecordingIntegration()
        summary = LiveHandoffCoordinator(
            plan=_plan(DependencyType.LIVE),
            dependencies=LiveHandoffDependencies(
                artifacts=artifacts,
                workers=workers,
                integration=integration,
            ),
            scenario="stale_control",
            mode="live_handoff",
            max_workers=2,
            timeout_seconds=2,
            run_id="stale-control",
        ).run()

        self.assertEqual(summary.status, "stale_dependency")
        self.assertFalse(summary.integration_passed)
        self.assertEqual(integration.calls, 0)
        self.assertEqual(summary.stale_dependencies[0]["consumed_version"], 1)
        self.assertEqual(summary.stale_dependencies[0]["latest_version"], 2)


class ControlledLiveHandoffExperimentTest(unittest.TestCase):
    def test_frozen_suite_produces_seven_private_path_free_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory) / "evidence"
            result = run_suite(
                plan_path=FROZEN_PLAN,
                output_root=output_root,
                stable_baseline_commit="stable-test-baseline",
            )
            self.assertTrue(result["overall_passed"])
            self.assertEqual(len(result["runs"]), 7)
            self.assertTrue(all(result["assertions"].values()))
            self.assertFalse(result["real_model_performance_evaluated"])
            self.assertTrue(
                (output_root / "real-agent-loop-integration.json").is_file()
            )
            sequential = next(
                run
                for run in result["runs"]
                if run["scenario"] == "bidirectional_schema"
                and run["mode"] == "sequential"
            )
            naive = next(
                run
                for run in result["runs"]
                if run["scenario"] == "bidirectional_schema"
                and run["mode"] == "naive_parallel"
            )
            self.assertTrue(sequential["integration_passed"])
            self.assertEqual(sequential["metrics"]["rework_count"], 2)
            self.assertFalse(naive["integration_passed"])
            self.assertEqual(naive["metrics"]["rework_count"], 0)

            result_text = (output_root / "result.json").read_text(encoding="utf-8")
            self.assertNotIn(str(output_root), result_text)
            for run in result["runs"]:
                summary_path = output_root / run["artifact"]
                timeline_path = output_root / run["timeline"]
                self.assertTrue(summary_path.is_file())
                self.assertTrue(timeline_path.is_file())
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                timeline = [
                    json.loads(line)
                    for line in timeline_path.read_text(encoding="utf-8").splitlines()
                ]
                self.assertEqual(summary["schema_version"], 1)
                self.assertEqual(
                    [item["sequence"] for item in timeline],
                    list(range(1, len(timeline) + 1)),
                )
                self.assertNotIn(str(output_root), json.dumps(summary))

    def test_jsonl_repository_is_a_formal_artifact_port(self) -> None:
        self.assertIn(LiveHandoffArtifactPort, JsonlLiveHandoffRepository.__bases__)

    def test_real_agent_loop_case_has_safe_boundary_diff_and_causal_evidence(
        self,
    ) -> None:
        result = run_real_agent_loop_case()

        self.assertTrue(result["overall_passed"])
        self.assertEqual(
            result["evidence_class"],
            "deterministic_real_agent_loop_integration",
        )
        self.assertFalse(result["performance_evaluated"])
        self.assertEqual(
            [event["event_type"] for event in result["handoff_events"]],
            ["READY", "FEEDBACK", "UPDATE"],
        )
        self.assertTrue(
            all(
                boundary["boundary"].startswith(("before_model", "after_model"))
                for boundary in result["agent_loop_boundaries"]
            )
        )
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("Operator steer for the current task", serialized)
        self.assertNotIn("/var/folders/", serialized)


if __name__ == "__main__":
    unittest.main()

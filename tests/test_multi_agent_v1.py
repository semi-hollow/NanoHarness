import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.multi_agent.adapters.fanout_files import FanoutFileRepository
from agent_forge.multi_agent.adapters.local_worker import (
    _criterion_results,
    _decision,
    finalizer_task_prompt,
    worker_task_prompt,
)
from agent_forge.multi_agent.application.dependencies import LiveFanoutDependencies
from agent_forge.multi_agent.application.fanout import FanoutCoordinator
from agent_forge.multi_agent.application.planning import AdaptivePlanner
from agent_forge.multi_agent.domain.live import (
    CriterionResult,
    FanoutCheckpoint,
    FanoutPlan,
    FinalizerResult,
    LiveSubagentResult,
    WorkerHandoff,
    project_worker_handoff,
)
from agent_forge.multi_agent.domain.planning import PlanningDecision
from agent_forge.observability.api import TraceRecorder
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import AgentResponse


class ScriptedPlannerModel:
    last_usage = None

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, tools):
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(response, dict) and "error" in response:
            return AgentResponse(None, [], error=response["error"])
        return AgentResponse(str(response), [])


def _decision_json(mode="single", tasks=None):
    return json.dumps(
        {
            "mode": mode,
            "reason": "bounded fixture decision",
            "global_acceptance_criteria": ["focused tests pass"],
            "tasks": tasks or [],
        }
    )


def _task(task_id, *, depends_on=None, scope=None, criteria=None):
    return {
        "id": task_id,
        "task": f"implement {task_id}",
        "depends_on": depends_on or [],
        "write_scope": scope or [f"{task_id}.py"],
        "allowed_tools": ["replace_text"],
        "acceptance_criteria": criteria or [f"{task_id} behavior passes"],
        "max_steps": 4,
    }


class AdaptivePlannerTest(unittest.TestCase):
    def _planner(self, responses):
        model = ScriptedPlannerModel(responses)
        return (
            AdaptivePlanner(
                model_factory=lambda: model,
                available_tools=["read_file", "replace_text"],
                max_fanout_tasks=4,
                max_steps=6,
            ),
            model,
        )

    def test_single_decision_does_not_force_fanout(self):
        planner, model = self._planner([_decision_json()])
        with tempfile.TemporaryDirectory() as tmp:
            outcome = planner.decide("fix one local function", tmp)

        self.assertEqual(outcome.decision.mode, "single")
        self.assertFalse(outcome.fallback_to_single)
        self.assertEqual(model.calls, 1)

    def test_valid_fanout_flows_through_typed_plan_validation(self):
        planner, _ = self._planner(
            [_decision_json("fanout", [_task("alpha"), _task("beta")])]
        )
        with tempfile.TemporaryDirectory() as tmp:
            outcome = planner.decide("update two modules", tmp)

        plan = outcome.decision.to_fanout_plan("update two modules")
        self.assertEqual([task.id for task in plan.tasks], ["alpha", "beta"])
        self.assertEqual(plan.tasks[0].acceptance_criteria, ["alpha behavior passes"])
        self.assertEqual(plan.global_acceptance_criteria, ["focused tests pass"])

    def test_malformed_output_gets_one_repair_then_falls_back_to_single(self):
        planner, model = self._planner(["not-json", "still-not-json"])
        with tempfile.TemporaryDirectory() as tmp:
            outcome = planner.decide("any task", tmp)

        self.assertTrue(outcome.fallback_to_single)
        self.assertIsNone(outcome.decision)
        self.assertEqual(model.calls, 2)

    def test_cycle_and_invalid_scope_are_rejected_after_bounded_repair(self):
        cyclic = _decision_json(
            "fanout",
            [
                _task("alpha", depends_on=["beta"]),
                _task("beta", depends_on=["alpha"]),
            ],
        )
        invalid_scope = _decision_json(
            "fanout",
            [_task("alpha", scope=["../outside"])],
        )
        for invalid in (cyclic, invalid_scope):
            with self.subTest(invalid=invalid):
                planner, model = self._planner([invalid, invalid])
                with tempfile.TemporaryDirectory() as tmp:
                    outcome = planner.decide("unsafe proposal", tmp)
                self.assertTrue(outcome.fallback_to_single)
                self.assertEqual(model.calls, 2)

    def test_provider_failure_falls_back_without_fake_plan(self):
        planner, model = self._planner(
            [{"error": {"code": "timeout", "retryable": True}}]
        )
        with tempfile.TemporaryDirectory() as tmp:
            outcome = planner.decide("task", tmp)

        self.assertTrue(outcome.fallback_to_single)
        self.assertIn("provider failure", outcome.failure)
        self.assertEqual(model.calls, 1)


class OneShotMergeFaultWorkspace:
    """Case 4 fault injection lives at the WorkspacePort boundary."""

    def __init__(self, *, always_fail_b=False, inject_b_fault=True):
        self.applied = []
        self.failed_once = False
        self.always_fail_b = always_fail_b
        self.inject_b_fault = inject_b_fault

    def head(self):
        return "base-sha"

    def status(self):
        return ""

    def diff(self):
        return "\n".join(self.applied)

    def apply_unified_diff(self, diff_text, *, check_only):
        is_b = diff_text.startswith("PATCH:B")
        should_fail = (
            self.inject_b_fault
            and is_b
            and (self.always_fail_b or not self.failed_once)
        )
        if check_only and should_fail:
            self.failed_once = True
            return False, "injected stale candidate"
        if not check_only:
            self.applied.append(diff_text)
        return True, ""


class DeterministicWorkerPort:
    def __init__(
        self,
        root,
        *,
        retry_failures=0,
        scope_violation=False,
        terminal_failures=None,
    ):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.retry_failures = retry_failures
        self.scope_violation = scope_violation
        self.terminal_failures = set(terminal_failures or [])
        self.calls = []
        self.base_diffs = {}
        self.handoffs = {}

    def run_worker(
        self,
        task,
        batch_index,
        base_diff_text,
        dependency_handoffs,
        attempt,
    ):
        self.calls.append((task.id, attempt))
        self.base_diffs[(task.id, attempt)] = base_diff_text
        self.handoffs[(task.id, attempt)] = [
            item.task_id for item in dependency_handoffs
        ]
        worker_dir = self.root / task.id / f"attempt-{attempt}"
        worker_dir.mkdir(parents=True, exist_ok=True)
        patch_path = worker_dir / "candidate.diff"
        artifact_path = worker_dir / "task_output.md"
        artifact_path.write_text(f"{task.id} attempt {attempt}\n", encoding="utf-8")
        if self.scope_violation and task.id == "A":
            result = LiveSubagentResult(
                task_id=task.id,
                status="scope_violation",
                attempt=attempt,
                retryable=True,
                failure_kind="transient_transport",
                touched_files=["outside.py"],
                artifact_path=str(artifact_path),
                error="escaped scope",
            )
        elif task.id in self.terminal_failures:
            result = LiveSubagentResult(
                task_id=task.id,
                status="failed",
                attempt=attempt,
                retryable=False,
                failure_kind="deterministic_fixture_failure",
                artifact_path=str(artifact_path),
                error="fixture terminal failure",
            )
        elif task.id == "B" and attempt <= self.retry_failures:
            result = LiveSubagentResult(
                task_id=task.id,
                status="failed",
                attempt=attempt,
                retryable=True,
                failure_kind="provider_transport",
                artifact_path=str(artifact_path),
                error="fixture transient failure",
            )
        else:
            patch = f"PATCH:{task.id}:{attempt}"
            patch_path.write_text(patch, encoding="utf-8")
            result = LiveSubagentResult(
                task_id=task.id,
                status="completed",
                attempt=attempt,
                touched_files=list(task.write_scope),
                candidate_diff_path=str(patch_path),
                candidate_diff_sha256=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
                artifact_path=str(artifact_path),
                final_answer=f"completed {task.id}",
                validation_evidence=[{"status": "passed", "kind": "fixture"}],
            )
        result.handoff = project_worker_handoff(result)
        return result

    def run_finalizer(self, plan, results):
        criteria = list(plan.global_acceptance_criteria)
        for task in plan.tasks:
            criteria.extend(task.acceptance_criteria)
        return FinalizerResult(
            decision="PASS",
            answer="FINAL: PASS",
            trace_path=str(self.root / "finalizer-trace.json"),
            usage_path=str(self.root / "finalizer-usage.json"),
            usage_summary={},
            criterion_results=[
                CriterionResult(criterion, "PASS", "fixture evidence")
                for criterion in dict.fromkeys(criteria)
            ],
        )

    def validate_recovery_diffs(self, diffs):
        return "\n".join(diff for _, diff in diffs)


class FixedReplanner:
    def __init__(self, decision):
        self.decision = decision
        self.calls = 0

    def replan(self, **kwargs):
        self.calls += 1
        return self.decision


def _fanout_plan(tasks):
    return FanoutPlan.from_mapping(
        {
            "goal": "mechanism fixture",
            "global_acceptance_criteria": ["all planned work completes"],
            "tasks": tasks,
        }
    )


def _planning_decision(tasks):
    return PlanningDecision.from_mapping(
        {
            "mode": "fanout",
            "reason": "replace failed remaining work",
            "global_acceptance_criteria": [],
            "tasks": tasks,
        },
        available_tools=["replace_text"],
    )


class FanoutV1MechanismTest(unittest.TestCase):
    def _run(
        self,
        root,
        plan,
        workspace,
        workers,
        *,
        replanner=None,
    ):
        trace = TraceRecorder(str(Path(root) / "trace.json"))
        coordinator = FanoutCoordinator(
            plan=plan,
            base_config=RuntimeConfig(workspace=str(root), max_steps=4),
            dependencies=LiveFanoutDependencies(
                events=trace,
                workspace=workspace,
                artifacts=FanoutFileRepository(Path(root) / "run"),
                workers=workers,
                replanner=replanner,
            ),
            max_workers=2,
        )
        summary = coordinator.run()
        trace.write()
        return summary, trace

    def test_dependency_receives_only_direct_stable_handoff_and_code_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = _fanout_plan(
                [
                    _task("A"),
                    _task("B"),
                    _task("C", depends_on=["A"]),
                ]
            )
            workers = DeterministicWorkerPort(root / "workers")
            summary, _ = self._run(root, plan, OneShotMergeFaultWorkspace(), workers)

            self.assertEqual(summary.status, "passed")
            self.assertEqual(workers.handoffs[("C", 1)], ["A"])
            self.assertNotIn("B", workers.handoffs[("C", 1)])
            self.assertIn("PATCH:A:1", workers.base_diffs[("C", 1)])
            self.assertIn("PATCH:B:2", workers.base_diffs[("C", 1)])
            prompt = worker_task_prompt(
                plan.goal,
                plan.tasks[2],
                [summary.results[0].handoff],
            )
            self.assertIn("Direct dependency handoffs", prompt)
            self.assertNotIn(
                "Conversation", json.dumps(summary.results[0].handoff.to_dict())
            )

    def test_merge_fault_discards_old_candidate_and_reruns_once_on_latest_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = _fanout_plan([_task("A"), _task("B")])
            workers = DeterministicWorkerPort(root / "workers")
            summary, trace = self._run(
                root, plan, OneShotMergeFaultWorkspace(), workers
            )

            self.assertEqual(summary.status, "passed")
            self.assertEqual(workers.calls, [("A", 1), ("B", 1), ("B", 2)])
            self.assertIn("PATCH:A:1", workers.base_diffs[("B", 2)])
            self.assertEqual(summary.results[1].attempt, 2)
            self.assertEqual(summary.attempt_results[1].status, "merge_conflict")
            self.assertTrue(
                any(
                    event["event_type"] == "serialized_conflict_retry"
                    for event in trace.events
                )
            )

    def test_merge_recovery_and_scope_violation_are_hard_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = _fanout_plan([_task("A"), _task("B")])
            workers = DeterministicWorkerPort(root / "merge-workers")
            failed, _ = self._run(
                root / "merge",
                plan,
                OneShotMergeFaultWorkspace(always_fail_b=True),
                workers,
            )
            self.assertEqual(failed.status, "conflict_resolution_required")
            self.assertEqual(
                [call for call in workers.calls if call[0] == "B"], [("B", 1), ("B", 2)]
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = _fanout_plan([_task("A")])
            workers = DeterministicWorkerPort(
                root / "scope-workers", scope_violation=True
            )
            failed, _ = self._run(root, plan, OneShotMergeFaultWorkspace(), workers)
            self.assertEqual(failed.status, "conflict_resolution_required")
            self.assertEqual(workers.calls, [("A", 1)])

    def test_retry_once_then_replan_freezes_completed_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = _fanout_plan([_task("A"), _task("B")])
            workers = DeterministicWorkerPort(root / "workers", retry_failures=2)
            replanner = FixedReplanner(
                _planning_decision([_task("B2", depends_on=["A"])])
            )
            summary, trace = self._run(
                root,
                plan,
                OneShotMergeFaultWorkspace(),
                workers,
                replanner=replanner,
            )

            self.assertEqual(summary.status, "passed")
            self.assertEqual(summary.replan_round, 1)
            self.assertEqual(replanner.calls, 1)
            self.assertEqual(
                [call for call in workers.calls if call[0] == "A"], [("A", 1)]
            )
            self.assertEqual(
                [call for call in workers.calls if call[0] == "B"], [("B", 1), ("B", 2)]
            )
            self.assertEqual(
                [task["id"] for task in summary.effective_plan["tasks"]], ["A", "B2"]
            )
            self.assertTrue(
                any(event["event_type"] == "replan_result" for event in trace.events)
            )

    def test_invalid_replan_fails_safely_without_rerunning_completed_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = _fanout_plan([_task("A"), _task("B")])
            workers = DeterministicWorkerPort(root / "workers", retry_failures=2)
            replanner = FixedReplanner(
                _planning_decision([_task("B2", depends_on=["missing"])])
            )
            summary, trace = self._run(
                root,
                plan,
                OneShotMergeFaultWorkspace(),
                workers,
                replanner=replanner,
            )

            self.assertEqual(summary.status, "partial_failure")
            self.assertEqual(
                [call for call in workers.calls if call[0] == "A"], [("A", 1)]
            )
            failed_events = [
                event
                for event in trace.events
                if event["event_type"] == "replan_result" and not event["success"]
            ]
            self.assertEqual(len(failed_events), 1)

    def test_resume_after_replan_restores_effective_remaining_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initial_plan = _fanout_plan([_task("A"), _task("B")])
            first_workers = DeterministicWorkerPort(
                root / "first-workers",
                retry_failures=2,
                terminal_failures={"B2"},
            )
            replanner = FixedReplanner(
                _planning_decision([_task("B2", depends_on=["A"])])
            )
            first_summary, _ = self._run(
                root / "first",
                initial_plan,
                OneShotMergeFaultWorkspace(),
                first_workers,
                replanner=replanner,
            )
            self.assertEqual(first_summary.status, "partial_failure")
            self.assertEqual(first_summary.replan_round, 1)

            resumed_root = root / "resumed"
            resumed_workers = DeterministicWorkerPort(resumed_root / "workers")
            resumed_trace = TraceRecorder(str(resumed_root / "trace.json"))
            forbidden_replanner = FixedReplanner(
                _planning_decision([_task("should-not-run")])
            )
            resumed = FanoutCoordinator(
                plan=initial_plan,
                base_config=RuntimeConfig(
                    workspace=str(resumed_root),
                    max_steps=4,
                ),
                dependencies=LiveFanoutDependencies(
                    events=resumed_trace,
                    workspace=OneShotMergeFaultWorkspace(inject_b_fault=False),
                    artifacts=FanoutFileRepository(resumed_root / "run"),
                    workers=resumed_workers,
                    replanner=forbidden_replanner,
                ),
                max_workers=2,
                resume_from=first_summary.summary_path,
            ).run()

            self.assertEqual(resumed.status, "passed")
            self.assertEqual(resumed.replan_round, 1)
            self.assertEqual(
                [task["id"] for task in resumed.effective_plan["tasks"]],
                ["A", "B2"],
            )
            self.assertEqual(resumed_workers.calls, [("B2", 1)])
            self.assertEqual(forbidden_replanner.calls, 0)
            self.assertTrue(resumed.results[0].resumed)

    def test_resume_rejects_live_effective_plan_created_by_replan(self):
        """初始 HARD Plan 也不能绕过 LIVE mailbox 不支持 replay 的边界。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initial_plan = _fanout_plan([_task("A"), _task("B")])
            live_effective_plan = FanoutPlan.from_mapping(
                {
                    "goal": initial_plan.goal,
                    "global_acceptance_criteria": list(
                        initial_plan.global_acceptance_criteria
                    ),
                    "tasks": [_task("B2"), _task("C2")],
                    "live_dependencies": [
                        {
                            "producer_task_id": "B2",
                            "target_task_id": "C2",
                            "semantic_key": "shared_contract",
                        }
                    ],
                }
            )
            prior_artifacts = FanoutFileRepository(root / "prior")
            checkpoint_path = prior_artifacts.write_checkpoint(
                FanoutCheckpoint(
                    plan_digest=initial_plan.digest,
                    base_head="base-sha",
                    results=[],
                    merged_task_ids=[],
                    status="partial_failure",
                    initial_plan_identity={
                        "digest": initial_plan.digest,
                        "goal": initial_plan.goal,
                    },
                    effective_plan=live_effective_plan,
                    effective_plan_digest=live_effective_plan.digest,
                    replan_round=1,
                )
            )

            coordinator = FanoutCoordinator(
                plan=initial_plan,
                base_config=RuntimeConfig(workspace=str(root), max_steps=4),
                dependencies=LiveFanoutDependencies(
                    events=TraceRecorder(str(root / "trace.json")),
                    workspace=OneShotMergeFaultWorkspace(inject_b_fault=False),
                    artifacts=FanoutFileRepository(root / "resumed"),
                    workers=DeterministicWorkerPort(root / "workers"),
                ),
                max_workers=2,
                resume_from=checkpoint_path,
            )

            with self.assertRaisesRegex(RuntimeError, "LIVE coordination resume"):
                coordinator.run()


class CriteriaFinalizerContractTest(unittest.TestCase):
    def test_criteria_are_preserved_and_missing_or_failed_results_prevent_pass(self):
        plan = _fanout_plan([_task("A", criteria=["A is correct"])])
        handoff = WorkerHandoff("A", "completed", "done")
        result = LiveSubagentResult(task_id="A", status="completed", handoff=handoff)
        prompt = finalizer_task_prompt(plan.goal, [result], plan=plan)
        self.assertIn("all planned work completes", prompt)
        self.assertIn("A is correct", prompt)

        passed = _criterion_results(
            "CRITERION 1: PASS | evidence\nCRITERION 2: PASS | evidence\nFINAL: PASS",
            ["all planned work completes", "A is correct"],
        )
        failed = _criterion_results(
            "CRITERION 1: PASS | evidence\nCRITERION 2: FAIL | broken\nFINAL: PASS",
            ["all planned work completes", "A is correct"],
        )
        unknown = _criterion_results(
            "CRITERION 1: PASS | evidence\nFINAL: PASS",
            ["all planned work completes", "A is correct"],
        )
        self.assertTrue(all(item.status == "PASS" for item in passed))
        self.assertEqual(failed[1].status, "FAIL")
        self.assertEqual(unknown[1].status, "UNKNOWN")
        self.assertEqual(_decision("FINAL: PASS"), "PASS")


if __name__ == "__main__":
    unittest.main()

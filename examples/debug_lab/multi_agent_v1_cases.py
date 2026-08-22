"""Generate five isolated, deterministic Multi-Agent V1 mechanism cases."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path

from agent_forge.infrastructure.atomic_json import atomic_write_json
from agent_forge.multi_agent.adapters.fanout_files import FanoutFileRepository
from agent_forge.multi_agent.application.dependencies import LiveFanoutDependencies
from agent_forge.multi_agent.application.live_fanout import LiveFanoutCoordinator
from agent_forge.multi_agent.application.planning import (
    AdaptivePlanner,
    write_planning_artifact,
)
from agent_forge.multi_agent.domain.live import (
    CriterionResult,
    FanoutPlan,
    FinalizerResult,
    LiveSubagentResult,
    project_worker_handoff,
)
from agent_forge.multi_agent.domain.planning import PlanningDecision
from agent_forge.multi_agent.wiring import LiveFanoutBuildRequest, build_live_fanout
from agent_forge.observability.api import TraceRecorder
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import AgentResponse
from agent_forge.runtime.wiring import ToolRegistryBuildRequest, build_registry

try:
    from .support import DeterministicFanoutModel, create_workspace
except ImportError:  # Direct ``python examples/debug_lab/...py`` execution.
    from support import DeterministicFanoutModel, create_workspace


class _SingleDecisionModel:
    last_usage = None

    def chat(self, messages, tools):
        return AgentResponse(
            json.dumps(
                {
                    "mode": "single",
                    "reason": "the change is local and highly coupled",
                    "global_acceptance_criteria": ["focused behavior is correct"],
                    "tasks": [],
                }
            ),
            [],
        )


class _FanoutDecisionModel:
    last_usage = None

    def __init__(self, plan: FanoutPlan) -> None:
        self.plan = plan

    def chat(self, messages, tools):
        tasks = []
        for task in self.plan.to_dict()["tasks"]:
            task = dict(task)
            task.setdefault("acceptance_criteria", [])
            tasks.append(task)
        return AgentResponse(
            json.dumps(
                {
                    "mode": "fanout",
                    "reason": "the fixture has isolated policy tasks",
                    "global_acceptance_criteria": [],
                    "tasks": tasks,
                }
            ),
            [],
        )


class _EvidenceWorkspace:
    """Deterministic integration port with an optional one-shot Case 4 fault."""

    def __init__(self, *, fail_once_for: str = "") -> None:
        self.applied: list[str] = []
        self.fail_once_for = fail_once_for
        self.fault_consumed = False

    def head(self) -> str:
        return "v1-mechanism-base"

    def status(self) -> str:
        return ""

    def diff(self) -> str:
        return "\n".join(self.applied)

    def apply_unified_diff(
        self,
        diff_text: str,
        *,
        check_only: bool,
    ) -> tuple[bool, str]:
        fault_matches = f"PATCH:{self.fail_once_for}:" in diff_text
        if check_only and fault_matches and not self.fault_consumed:
            self.fault_consumed = True
            return False, "explicit one-shot merge-applicability fault injection"
        if not check_only:
            self.applied.append(diff_text)
        return True, ""


class _MechanismWorkers:
    def __init__(
        self,
        root: Path,
        *,
        transient_failures: dict[str, int] | None = None,
    ) -> None:
        self.root = root
        self.transient_failures = transient_failures or {}
        self.calls: list[dict[str, object]] = []

    def run_worker(
        self,
        task,
        batch_index,
        base_diff_text,
        dependency_handoffs,
        attempt,
    ):
        started = time.time()
        worker_dir = self.root / "workers" / task.id / f"attempt-{attempt}"
        worker_dir.mkdir(parents=True, exist_ok=True)
        artifact = worker_dir / "task_output.md"
        artifact.write_text(f"{task.id} attempt {attempt}\n", encoding="utf-8")
        call = {
            "task_id": task.id,
            "attempt": attempt,
            "batch_index": batch_index,
            "started_at": started,
            "base_diff": base_diff_text,
            "dependency_handoff_ids": [
                handoff.task_id for handoff in dependency_handoffs
            ],
        }
        self.calls.append(call)
        if attempt <= self.transient_failures.get(task.id, 0):
            worker_result = LiveSubagentResult(
                task_id=task.id,
                status="failed",
                attempt=attempt,
                retryable=True,
                failure_kind="provider_transport",
                error="deterministic transient provider failure",
                artifact_path=str(artifact),
            )
        else:
            patch_text = f"PATCH:{task.id}:{attempt}"
            patch_path = worker_dir / "candidate_changes.diff"
            patch_path.write_text(patch_text, encoding="utf-8")
            worker_result = LiveSubagentResult(
                task_id=task.id,
                status="completed",
                attempt=attempt,
                final_answer=f"completed {task.id}",
                touched_files=list(task.write_scope),
                candidate_diff_path=str(patch_path),
                candidate_diff_sha256=hashlib.sha256(
                    patch_text.encode("utf-8")
                ).hexdigest(),
                artifact_path=str(artifact),
                validation_evidence=[{"kind": "mechanism_fixture", "status": "passed"}],
            )
        worker_result.handoff = project_worker_handoff(worker_result)
        return worker_result

    def run_finalizer(self, plan, results):
        criteria = list(plan.global_acceptance_criteria)
        for task in plan.tasks:
            criteria.extend(task.acceptance_criteria)
        return FinalizerResult(
            decision="PASS",
            answer="FINAL: PASS\nAll deterministic mechanism criteria passed.",
            trace_path=str(self.root / "finalizer" / "trace.json"),
            usage_path=str(self.root / "finalizer" / "usage.json"),
            usage_summary={},
            criterion_results=[
                CriterionResult(criterion, "PASS", "deterministic evidence")
                for criterion in dict.fromkeys(criteria)
            ],
        )

    def validate_recovery_diffs(self, diffs):
        return "\n".join(diff_text for _, diff_text in diffs)


class _OneRoundReplanner:
    def __init__(self) -> None:
        self.calls = 0

    def replan(self, **kwargs):
        self.calls += 1
        return PlanningDecision.from_mapping(
            {
                "mode": "fanout",
                "reason": "replace the exhausted transient task",
                "global_acceptance_criteria": [],
                "tasks": [
                    {
                        "id": "B2",
                        "task": "finish B on the current integrated state",
                        "depends_on": ["A"],
                        "write_scope": ["b2.py"],
                        "allowed_tools": ["replace_text"],
                        "acceptance_criteria": ["B2 completes"],
                        "max_steps": 4,
                    }
                ],
            },
            available_tools=["replace_text"],
        )


def _task(task_id: str, *, depends_on: list[str] | None = None) -> dict[str, object]:
    return {
        "id": task_id,
        "task": f"implement {task_id}",
        "depends_on": depends_on or [],
        "write_scope": [f"{task_id.lower()}.py"],
        "allowed_tools": ["replace_text"],
        "acceptance_criteria": [f"{task_id} completes"],
        "max_steps": 4,
    }


def _plan(tasks: list[dict[str, object]]) -> FanoutPlan:
    return FanoutPlan.from_mapping(
        {
            "goal": "prove one bounded orchestration mechanism",
            "global_acceptance_criteria": ["all effective tasks complete"],
            "tasks": tasks,
        }
    )


def _real_lab_plan(*, include_verifier: bool) -> FanoutPlan:
    tasks: list[dict[str, object]] = [
        {
            "id": "pricing-policy",
            "task": "Repair pricing.py and reject invalid pricing inputs.",
            "write_scope": ["pricing.py"],
            "allowed_tools": ["read_file", "replace_text", "git_diff"],
            "max_steps": 5,
        },
        {
            "id": "shipping-policy",
            "task": "Repair shipping.py without breaking expedited-fee behavior.",
            "write_scope": ["shipping.py"],
            "allowed_tools": ["read_file", "replace_text", "git_diff"],
            "max_steps": 5,
        },
    ]
    if include_verifier:
        tasks.append(
            {
                "id": "edge-case-verifier",
                "task": "Validate test_checkout.py after both policies are integrated.",
                "depends_on": ["pricing-policy", "shipping-policy"],
                "write_scope": [],
                "allowed_tools": ["python_validation", "git_diff"],
                "max_steps": 4,
            }
        )
    return FanoutPlan.from_mapping(
        {
            "goal": "Repair and validate the checkout policy fixture.",
            "tasks": tasks,
        }
    )


def _plan_real_lab_case(
    case_dir: Path,
    *,
    include_verifier: bool,
) -> tuple[FanoutPlan, str]:
    proposed_plan = _real_lab_plan(include_verifier=include_verifier)
    planner = AdaptivePlanner(
        model_factory=lambda: _FanoutDecisionModel(proposed_plan),
        available_tools=[
            "read_file",
            "replace_text",
            "git_diff",
            "python_validation",
        ],
        max_fanout_tasks=4,
        max_steps=6,
    )
    planning_outcome = planner.decide(proposed_plan.goal, case_dir)
    planning_path = write_planning_artifact(
        case_dir / "planning_decision.json", planning_outcome
    )
    if planning_outcome.decision is None:
        raise RuntimeError(f"fixture Planner failed: {planning_outcome.failure}")
    return (
        planning_outcome.decision.to_fanout_plan(proposed_plan.goal),
        str(planning_path),
    )


def _run_real_lab_case(case_dir: Path, plan: FanoutPlan) -> dict[str, object]:
    workspace = create_workspace(
        case_dir.name,
        template_root=Path(__file__).resolve().parent / "multi_agent_repository",
        state_root=case_dir / "fixture_state",
    )
    trace = TraceRecorder(str(case_dir / "trace.json"))

    def registry_factory(worktree, environment):
        return build_registry(
            ToolRegistryBuildRequest(
                workspace=str(worktree),
                auto=True,
                execution_environment=environment,
                memory_root=str(case_dir / "disabled_memory"),
                memory_namespace=str(worktree.resolve()),
            )
        )

    summary = build_live_fanout(
        LiveFanoutBuildRequest(
            plan=plan,
            base_config=RuntimeConfig(
                workspace=str(workspace),
                max_steps=6,
                auto_approve_writes=True,
                approval_mode="trusted",
                tool_routing_mode="all",
                skill_mode="none",
                memory_max_chars=0,
            ),
            trace=trace,
            run_dir=case_dir,
            llm_factory=DeterministicFanoutModel,
            registry_factory=registry_factory,
            max_workers=2,
        )
    ).run()
    trace.write()
    task_by_id = {task.id: task for task in plan.tasks}
    worker_calls: list[dict[str, object]] = []
    for result in summary.results:
        worker_trace = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
        worker_prompt = str(worker_trace.get("task") or "")
        environment = json.loads(
            Path(result.environment_manifest_path).read_text(encoding="utf-8")
        )
        worker_calls.append(
            {
                "task_id": result.task_id,
                "attempt": result.attempt,
                "batch_index": result.batch_index,
                "environment_mode": environment.get("probe", {}).get("mode"),
                "workspace_removed_after_run": not Path(result.workspace).exists(),
                "dependency_handoff_ids": [
                    dependency
                    for dependency in task_by_id[result.task_id].depends_on
                    if f'"task_id": "{dependency}"' in worker_prompt
                ],
                "validation_evidence": result.validation_evidence,
            }
        )
    evidence = {
        "case": case_dir.name,
        "status": summary.status,
        "summary_path": summary.summary_path,
        "trace_path": str(case_dir / "trace.json"),
        "batches": summary.batches,
        "worker_calls": worker_calls,
        "replan_round": summary.replan_round,
        "final_decision": summary.final_decision,
        "mechanism_only": True,
        "benchmark_claim": "none",
    }
    atomic_write_json(case_dir / "mechanism_evidence.json", evidence)
    return evidence


def _run_fanout_case(
    case_dir: Path,
    plan: FanoutPlan,
    *,
    workspace: _EvidenceWorkspace,
    workers: _MechanismWorkers,
    replanner=None,
) -> dict[str, object]:
    trace = TraceRecorder(str(case_dir / "trace.json"))
    summary = LiveFanoutCoordinator(
        plan=plan,
        base_config=RuntimeConfig(workspace=str(case_dir), max_steps=4),
        dependencies=LiveFanoutDependencies(
            events=trace,
            workspace=workspace,
            artifacts=FanoutFileRepository(case_dir),
            workers=workers,
            replanner=replanner,
        ),
        max_workers=2,
    ).run()
    trace.write()
    evidence = {
        "case": case_dir.name,
        "status": summary.status,
        "summary_path": summary.summary_path,
        "trace_path": str(case_dir / "trace.json"),
        "worker_calls": workers.calls,
        "replan_round": summary.replan_round,
        "final_decision": summary.final_decision,
        "mechanism_only": True,
        "benchmark_claim": "none",
    }
    atomic_write_json(case_dir / "mechanism_evidence.json", evidence)
    return evidence


def _record_case_checks(
    case_dir: Path,
    evidence: dict[str, object],
    checks: dict[str, bool],
    **details: object,
) -> dict[str, object]:
    evidence["checks"] = checks
    evidence.update(details)
    if evidence["status"] != "passed" or not all(checks.values()):
        evidence["status"] = "failed"
    atomic_write_json(case_dir / "mechanism_evidence.json", evidence)
    return evidence


def run_cases(output_root: Path) -> Path:
    run_dir = output_root / (
        f"{time.strftime('run-%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:7]}"
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    cases: list[dict[str, object]] = []

    case_1 = run_dir / "case-1-single-gate"
    case_1.mkdir()
    planner = AdaptivePlanner(
        model_factory=_SingleDecisionModel,
        available_tools=["read_file", "replace_text"],
        max_fanout_tasks=4,
        max_steps=4,
    )
    planning_outcome = planner.decide("change one local function", case_1)
    planning_path = write_planning_artifact(
        case_1 / "planning_decision.json", planning_outcome
    )
    case_1_trace = TraceRecorder(str(case_1 / "trace.json"))
    case_1_trace.add(
        0,
        "AdaptivePlanner",
        "planning_decision",
        planning=planning_outcome.to_dict(),
    )
    case_1_trace.set_run_context(
        task="change one local function",
        stop_reason="planning_single",
        stop_output="Planner selected the existing Single Harness route.",
    )
    case_1_trace.write()
    case_1_evidence: dict[str, object] = {
        "case": case_1.name,
        "status": "passed" if planning_outcome.decision.mode == "single" else "failed",
        "planning_path": str(planning_path),
        "trace_path": str(case_1 / "trace.json"),
        "mechanism_only": True,
        "benchmark_claim": "none",
    }
    cases.append(
        _record_case_checks(
            case_1,
            case_1_evidence,
            {"planner_selected_single": planning_outcome.decision.mode == "single"},
        )
    )

    case_2 = run_dir / "case-2-parallel-fanout"
    case_2.mkdir()
    case_2_plan, case_2_planning_path = _plan_real_lab_case(
        case_2, include_verifier=False
    )
    case_2_evidence = _run_real_lab_case(case_2, case_2_plan)
    cases.append(
        _record_case_checks(
            case_2,
            case_2_evidence,
            {
                "independent_tasks_share_one_parallel_batch": case_2_evidence["batches"]
                == [["pricing-policy", "shipping-policy"]],
                "workers_used_isolated_worktrees": all(
                    call["environment_mode"] == "worktree"
                    and call["workspace_removed_after_run"] is True
                    for call in case_2_evidence["worker_calls"]
                ),
                "integration_passed": case_2_evidence["status"] == "passed",
                "finalizer_passed": case_2_evidence["final_decision"] == "PASS",
                "planner_selected_fanout": len(case_2_plan.tasks) == 2,
            },
            planning_path=case_2_planning_path,
        )
    )

    case_3 = run_dir / "case-3-dependency-handoff"
    case_3.mkdir()
    case_3_plan, case_3_planning_path = _plan_real_lab_case(
        case_3, include_verifier=True
    )
    case_3_evidence = _run_real_lab_case(case_3, case_3_plan)
    case_3_downstream = next(
        call
        for call in case_3_evidence["worker_calls"]
        if call["task_id"] == "edge-case-verifier"
    )
    cases.append(
        _record_case_checks(
            case_3,
            case_3_evidence,
            {
                "direct_handoffs_are_declared_dependencies": case_3_downstream[
                    "dependency_handoff_ids"
                ]
                == ["pricing-policy", "shipping-policy"],
                "integrated_code_state_passed_downstream_validation": any(
                    str(item.get("status") or "").lower() == "passed"
                    for item in case_3_downstream["validation_evidence"]
                ),
                "finalizer_passed": case_3_evidence["final_decision"] == "PASS",
                "planner_selected_fanout": len(case_3_plan.tasks) == 3,
            },
            planning_path=case_3_planning_path,
        )
    )

    case_4 = run_dir / "case-4-merge-fault-recovery"
    case_4.mkdir()
    case_4_workspace = _EvidenceWorkspace(fail_once_for="B")
    case_4_workers = _MechanismWorkers(case_4)
    case_4_evidence = _run_fanout_case(
        case_4,
        _plan([_task("A"), _task("B")]),
        workspace=case_4_workspace,
        workers=case_4_workers,
    )
    case_4_b_calls = [call for call in case_4_workers.calls if call["task_id"] == "B"]
    cases.append(
        _record_case_checks(
            case_4,
            case_4_evidence,
            {
                "fault_was_consumed": case_4_workspace.fault_consumed,
                "old_candidate_was_replaced_once": [
                    call["attempt"] for call in case_4_b_calls
                ]
                == [1, 2],
                "fresh_worker_saw_latest_state": bool(case_4_b_calls)
                and "PATCH:A:1" in str(case_4_b_calls[-1]["base_diff"]),
            },
            fault_injection={
                "kind": "deterministic_one_shot_merge_applicability",
                "boundary": "FanoutWorkspacePort.apply_unified_diff(check_only=True)",
                "natural_git_conflict": False,
            },
        )
    )

    case_5 = run_dir / "case-5-retry-replan"
    case_5.mkdir()
    case_5_workers = _MechanismWorkers(case_5, transient_failures={"B": 2})
    case_5_replanner = _OneRoundReplanner()
    case_5_evidence = _run_fanout_case(
        case_5,
        _plan([_task("A"), _task("B")]),
        workspace=_EvidenceWorkspace(),
        workers=case_5_workers,
        replanner=case_5_replanner,
    )
    cases.append(
        _record_case_checks(
            case_5,
            case_5_evidence,
            {
                "worker_retry_is_exactly_one": [
                    call["attempt"]
                    for call in case_5_workers.calls
                    if call["task_id"] == "B"
                ]
                == [1, 2],
                "replan_is_exactly_one": case_5_replanner.calls == 1
                and case_5_evidence["replan_round"] == 1,
                "completed_prefix_A_was_not_rerun": sum(
                    call["task_id"] == "A" for call in case_5_workers.calls
                )
                == 1,
            },
        )
    )

    aggregate = {
        "schema_version": 1,
        "suite": "multi-agent-v1-mechanisms",
        "status": "passed"
        if all(case["status"] == "passed" for case in cases)
        else "failed",
        "cases": cases,
        "claim_boundary": (
            "These cases prove bounded orchestration mechanisms only; they do not "
            "claim a Multi-Agent Pass@1 improvement."
        ),
    }
    summary_path = run_dir / "mechanism_cases_summary.json"
    atomic_write_json(summary_path, aggregate)
    return summary_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        default=".agent_forge/runs/v1-multi-agent",
        help="Independent V1 evidence root; no canonical/latest pointer is updated.",
    )
    args = parser.parse_args()
    print(run_cases(Path(args.output_root).resolve()))


if __name__ == "__main__":
    main()

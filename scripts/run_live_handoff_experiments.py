#!/usr/bin/env python3
"""Run the frozen Live Handoff mechanism cases and persist auditable timelines."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from agent_forge.atomic_json import atomic_write_json
from agent_forge.multi_agent.api import (
    DependencyType,
    HandoffSeverity,
    LiveDependency,
    LiveEventType,
    LiveHandoffBuildRequest,
    LiveHandoffEvent,
    LiveHandoffPlan,
    LiveHandoffSummary,
    LiveWorkerCandidate,
    SubagentTask,
    build_live_handoff,
)
from agent_forge.multi_agent.ports import (
    LiveHandoffWorkerPort,
    LiveIntegrationPort,
    LiveWorkerContextPort,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_EXPERIMENT_ROOT = (
    PROJECT_ROOT / "benchmarks" / "experiments" / "live-handoff-v1"
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / ".agent_forge" / "runs" / "live-handoff-v1"
EXPECTED_RUNS = (
    ("early_unblock", "sequential"),
    ("early_unblock", "naive_parallel"),
    ("early_unblock", "live_handoff"),
    ("bidirectional_schema", "sequential"),
    ("bidirectional_schema", "naive_parallel"),
    ("bidirectional_schema", "live_handoff"),
    ("hard_dependency_control", "hard_dependency"),
)


@dataclass
class ExperimentState:
    """Thread-safe fixture state shared only inside one controlled run."""

    sources: dict[str, str] = field(default_factory=dict)
    lock: RLock = field(default_factory=RLock)

    def set_source(self, task_id: str, source: str) -> None:
        with self.lock:
            self.sources[task_id] = source

    def get_source(self, task_id: str) -> str:
        with self.lock:
            return self.sources.get(task_id, "")


class ControlledWorker(LiveHandoffWorkerPort):
    """Execute one frozen micro-case in an isolated worker directory."""

    def __init__(
        self,
        *,
        scenario: str,
        mode: str,
        settings: Mapping[str, Any],
        workspace_root: Path,
        state: ExperimentState,
    ) -> None:
        self.scenario = scenario
        self.mode = mode
        self.settings = settings
        self.workspace_root = workspace_root
        self.state = state

    def run_worker(
        self,
        task: SubagentTask,
        context: LiveWorkerContextPort,
    ) -> LiveWorkerCandidate:
        if self.scenario == "early_unblock":
            return self._run_early_unblock(task, context)
        if self.scenario == "bidirectional_schema":
            return self._run_bidirectional_schema(task, context)
        if self.scenario == "hard_dependency_control":
            return self._run_hard_control(task, context)
        raise ValueError(f"unknown controlled scenario: {self.scenario}")

    def _run_early_unblock(
        self,
        task: SubagentTask,
        context: LiveWorkerContextPort,
    ) -> LiveWorkerCandidate:
        if task.id == "producer":
            _sleep_ms(self.settings["producer_before_ready"])
            source = (
                "def fetch_user(user_id: str, *, timeout_ms: int = 1000) -> dict:\n"
                "    return {'id': user_id, 'timeout_ms': timeout_ms}\n"
            )
            self.state.set_source(task.id, source)
            self._write_source(task.id, "core_api.py", source)
            context.record_action(
                "api_contract_materialized",
                artifact="workers/producer/core_api.py",
                signature="fetch_user(user_id, *, timeout_ms=1000)",
            )
            if self.mode == "live_handoff":
                accepted = context.publish(
                    LiveHandoffEvent(
                        event_type=LiveEventType.READY,
                        producer_task_id="producer",
                        target_task_id="consumer",
                        semantic_key="api_contract",
                        version=1,
                        summary="The migrated API requires keyword-only timeout_ms.",
                        evidence=("signature=fetch_user(user_id, *, timeout_ms=1000)",),
                    )
                )
                if not accepted:
                    raise RuntimeError("Runtime rejected the frozen READY event")
            _sleep_ms(self.settings["producer_after_ready"])
            return _candidate("core_api.py", source)

        milestone_events = []
        if self.mode == "live_handoff":
            milestone_events = context.drain_mailbox(boundary="before_first_model_turn")
        uses_keyword_contract = any(
            event.event_type == LiveEventType.READY
            and event.semantic_key == "api_contract"
            and event.version == 1
            for event in milestone_events
        )
        if self.mode == "sequential":
            uses_keyword_contract = "timeout_ms" in self.state.get_source("producer")
        source = (
            "def load_user(fetch_user):\n    return fetch_user('u-1', timeout_ms=250)\n"
            if uses_keyword_contract
            else "def load_user(fetch_user):\n    return fetch_user('u-1', 250)\n"
        )
        self._write_source(task.id, "sdk_caller.py", source)
        context.record_action(
            "caller_migrated",
            contract="keyword_timeout"
            if uses_keyword_contract
            else "guessed_positional",
            artifact="workers/consumer/sdk_caller.py",
        )
        _sleep_ms(self.settings["consumer_work"])
        return _candidate("sdk_caller.py", source)

    def _run_bidirectional_schema(
        self,
        task: SubagentTask,
        context: LiveWorkerContextPort,
    ) -> LiveWorkerCandidate:
        if task.id == "producer":
            return self._run_schema_producer(context)
        return self._run_schema_consumer(context)

    def _run_schema_producer(
        self,
        context: LiveWorkerContextPort,
    ) -> LiveWorkerCandidate:
        _sleep_ms(self.settings["producer_before_ready"])
        source = _schema_v1_source()
        self.state.set_source("producer", source)
        self._write_source("producer", "config_schema.py", source)
        context.record_action(
            "initial_schema_materialized",
            accepted_keys=["timeout"],
            artifact="workers/producer/config_schema.py",
        )
        if self.mode != "live_handoff":
            _sleep_ms(self.settings["producer_after_update"])
            return _candidate("config_schema.py", source)

        accepted = context.publish(
            LiveHandoffEvent(
                event_type=LiveEventType.READY,
                producer_task_id="producer",
                target_task_id="consumer",
                semantic_key="config_schema",
                version=1,
                summary="Config schema v1 accepts timeout only.",
                evidence=("accepted_keys=timeout",),
            )
        )
        if not accepted:
            raise RuntimeError("Runtime rejected schema READY(v1)")

        trajectory_changed = False
        for boundary_index in range(30):
            _sleep_ms(self.settings["safe_boundary_poll"])
            feedback = context.drain_mailbox(
                boundary=f"after_tool_observation_{boundary_index + 1}"
            )
            if not any(
                event.event_type == LiveEventType.FEEDBACK
                and event.semantic_key == "config_schema"
                and event.version == 1
                for event in feedback
            ):
                continue
            trajectory_changed = True
            context.record_action(
                "trajectory_changed_from_feedback",
                old_accepted_keys=["timeout"],
                new_accepted_keys=["timeout", "legacy_timeout"],
            )
            source = _schema_v2_source()
            self.state.set_source("producer", source)
            self._write_source("producer", "config_schema.py", source)
            accepted = context.publish(
                LiveHandoffEvent(
                    event_type=LiveEventType.UPDATE,
                    producer_task_id="producer",
                    target_task_id="consumer",
                    semantic_key="config_schema",
                    version=2,
                    summary="Schema v2 maps legacy_timeout into timeout.",
                    evidence=(
                        "accepted_keys=timeout,legacy_timeout",
                        "legacy_timeout maps to timeout",
                    ),
                )
            )
            if not accepted:
                raise RuntimeError("Runtime rejected schema UPDATE(v2)")
            break
        if not trajectory_changed:
            raise RuntimeError("producer did not receive feedback at a safe boundary")

        _sleep_ms(self.settings["producer_after_update"])
        return _candidate(
            "config_schema.py",
            source,
            trajectory_changed=True,
            rework_count=1,
        )

    def _run_schema_consumer(
        self,
        context: LiveWorkerContextPort,
    ) -> LiveWorkerCandidate:
        received_v2 = False
        if self.mode == "live_handoff":
            ready_events = context.drain_mailbox(boundary="before_first_model_turn")
            if not any(
                event.event_type == LiveEventType.READY
                and event.semantic_key == "config_schema"
                and event.version == 1
                for event in ready_events
            ):
                raise RuntimeError("consumer started without READY(v1)")

        _sleep_ms(self.settings["consumer_discovery"])
        context.record_action(
            "downstream_constraint_discovered",
            constraint="deployed configs still use legacy_timeout",
        )
        if self.mode == "live_handoff":
            accepted = context.publish(
                LiveHandoffEvent(
                    event_type=LiveEventType.FEEDBACK,
                    producer_task_id="consumer",
                    target_task_id="producer",
                    semantic_key="config_schema",
                    version=1,
                    summary="Consumer migration requires legacy_timeout compatibility.",
                    evidence=("fixture=configs/service-a.yaml uses legacy_timeout",),
                    severity=HandoffSeverity.BLOCKING,
                )
            )
            if not accepted:
                raise RuntimeError("Runtime rejected blocking FEEDBACK(v1)")
            for boundary_index in range(30):
                _sleep_ms(self.settings["safe_boundary_poll"])
                updates = context.drain_mailbox(
                    boundary=f"before_next_model_turn_{boundary_index + 1}"
                )
                if any(
                    event.event_type == LiveEventType.UPDATE
                    and event.semantic_key == "config_schema"
                    and event.version == 2
                    for event in updates
                ):
                    received_v2 = True
                    context.record_action(
                        "consumer_revalidated_against_update",
                        consumed_version=2,
                    )
                    break
            if not received_v2:
                raise RuntimeError("consumer did not receive UPDATE(v2)")

        source = (
            "def boot_service(normalize_config):\n"
            "    return normalize_config({'legacy_timeout': '30'})\n"
        )
        self._write_source("consumer", "service_consumer.py", source)
        _sleep_ms(self.settings["consumer_after_update"])
        return _candidate(
            "service_consumer.py",
            source,
            rework_count=0 if received_v2 else 1,
            trajectory_changed=received_v2,
        )

    def _run_hard_control(
        self,
        task: SubagentTask,
        context: LiveWorkerContextPort,
    ) -> LiveWorkerCandidate:
        if task.id == "producer":
            _sleep_ms(self.settings["producer_work"])
            source = "GENERATED_TOKEN = 'token-v1'\n"
            self.state.set_source(task.id, source)
            self._write_source(task.id, "generated_constants.py", source)
            context.record_action("generated_constant_finalized", token="token-v1")
            return _candidate("generated_constants.py", source)

        if "GENERATED_TOKEN" not in self.state.get_source("producer"):
            raise RuntimeError("hard dependency consumer started before output existed")
        source = "def read_token():\n    return GENERATED_TOKEN\n"
        self._write_source(task.id, "constant_consumer.py", source)
        context.record_action("completed_output_consumed", token="token-v1")
        _sleep_ms(self.settings["consumer_work"])
        return _candidate("constant_consumer.py", source)

    def _write_source(self, task_id: str, filename: str, source: str) -> None:
        worker_root = self.workspace_root / task_id
        worker_root.mkdir(parents=True, exist_ok=True)
        (worker_root / filename).write_text(source, encoding="utf-8")


class ControlledIntegration(LiveIntegrationPort):
    """Run a real Python assertion over the isolated candidate sources."""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario

    def validate(
        self,
        candidates: Mapping[str, LiveWorkerCandidate],
    ) -> tuple[bool, str]:
        try:
            producer = candidates["producer"].payload["source"]
            consumer = candidates["consumer"].payload["source"]
            namespace: dict[str, Any] = {}
            exec(compile(producer, "producer_candidate.py", "exec"), namespace)
            exec(compile(consumer, "consumer_candidate.py", "exec"), namespace)
            if self.scenario == "early_unblock":
                actual = namespace["load_user"](namespace["fetch_user"])
                assert actual == {"id": "u-1", "timeout_ms": 250}
            elif self.scenario == "bidirectional_schema":
                actual = namespace["boot_service"](namespace["normalize_config"])
                assert actual == {"timeout": 30}
            else:
                assert namespace["read_token"]() == "token-v1"
        except (AssertionError, KeyError, TypeError, ValueError) as exc:
            return False, f"integration assertion failed: {type(exc).__name__}: {exc}"
        return True, "integration assertion passed"


def _candidate(
    artifact: str,
    source: str,
    *,
    retry_count: int = 0,
    rework_count: int = 0,
    trajectory_changed: bool = False,
) -> LiveWorkerCandidate:
    compile(source, artifact, "exec")
    return LiveWorkerCandidate(
        payload={"artifact": artifact, "source": source},
        test_passed=True,
        retry_count=retry_count,
        rework_count=rework_count,
        trajectory_changed=trajectory_changed,
    )


def _schema_v1_source() -> str:
    return (
        "def normalize_config(config):\n"
        "    allowed = {'timeout'}\n"
        "    unknown = set(config) - allowed\n"
        "    if unknown:\n"
        "        raise ValueError(f'unknown config keys: {sorted(unknown)}')\n"
        "    return {'timeout': int(config['timeout'])}\n"
    )


def _schema_v2_source() -> str:
    return (
        "def normalize_config(config):\n"
        "    normalized = dict(config)\n"
        "    if 'legacy_timeout' in normalized and 'timeout' not in normalized:\n"
        "        normalized['timeout'] = normalized.pop('legacy_timeout')\n"
        "    allowed = {'timeout'}\n"
        "    unknown = set(normalized) - allowed\n"
        "    if unknown:\n"
        "        raise ValueError(f'unknown config keys: {sorted(unknown)}')\n"
        "    return {'timeout': int(normalized['timeout'])}\n"
    )


def _sleep_ms(milliseconds: int) -> None:
    time.sleep(int(milliseconds) / 1_000)


def _make_plan(scenario: str, mode: str, semantic_key: str | None) -> LiveHandoffPlan:
    tasks = (
        SubagentTask(
            id="producer",
            task=f"Produce the upstream artifact for {scenario}.",
            write_scope=["producer/"],
            allowed_tools=["write_file", "python"],
            expected_artifact="producer_candidate",
            max_steps=8,
        ),
        SubagentTask(
            id="consumer",
            task=f"Migrate the downstream consumer for {scenario}.",
            write_scope=["consumer/"],
            allowed_tools=["write_file", "python"],
            expected_artifact="consumer_candidate",
            max_steps=8,
        ),
    )
    dependencies: tuple[LiveDependency, ...]
    if mode in {"sequential", "hard_dependency"}:
        dependencies = (
            LiveDependency(
                producer_task_id="producer",
                target_task_id="consumer",
                dependency_type=DependencyType.HARD,
            ),
        )
    elif mode == "live_handoff":
        if semantic_key is None:
            raise ValueError("LIVE experiment requires a semantic key")
        dependencies = (
            LiveDependency(
                producer_task_id="producer",
                target_task_id="consumer",
                dependency_type=DependencyType.LIVE,
                semantic_key=semantic_key,
            ),
        )
    else:
        dependencies = ()
    return LiveHandoffPlan(
        goal=f"Controlled {scenario} run in {mode} mode.",
        tasks=tasks,
        dependencies=dependencies,
    )


def _load_frozen_plan(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("controlled experiment plan must use schema_version 1")
    cases = {case["id"]: case for case in payload.get("frozen_cases", [])}
    actual_runs = tuple(
        (case["id"], mode)
        for case in payload.get("frozen_cases", [])
        for mode in case["modes"]
    )
    if actual_runs != EXPECTED_RUNS:
        raise ValueError(
            "controlled experiment plan no longer matches the frozen run set"
        )
    return payload, cases


def _run_one(
    *,
    scenario: str,
    mode: str,
    case: Mapping[str, Any],
    output_root: Path,
) -> LiveHandoffSummary:
    run_id = f"{scenario}-{mode}"
    run_root = output_root / "runs" / run_id
    state = ExperimentState()
    worker = ControlledWorker(
        scenario=scenario,
        mode=mode,
        settings=case["timing_ms"],
        workspace_root=run_root / "workers",
        state=state,
    )
    coordinator = build_live_handoff(
        LiveHandoffBuildRequest(
            plan=_make_plan(scenario, mode, case.get("semantic_key")),
            scenario=scenario,
            mode=mode,
            run_dir=run_root,
            workers=worker,
            integration=ControlledIntegration(scenario),
            max_workers=2,
            timeout_seconds=5.0,
            run_id=run_id,
        )
    )
    return coordinator.run()


def _worker_result(summary: LiveHandoffSummary, task_id: str) -> Any:
    return next(result for result in summary.results if result.task_id == task_id)


def _evaluate_assertions(
    runs: Mapping[tuple[str, str], LiveHandoffSummary],
) -> dict[str, bool]:
    case1_live = runs[("early_unblock", "live_handoff")]
    case1_sequential = runs[("early_unblock", "sequential")]
    case1_naive = runs[("early_unblock", "naive_parallel")]
    case2_live = runs[("bidirectional_schema", "live_handoff")]
    case2_sequential = runs[("bidirectional_schema", "sequential")]
    case2_naive = runs[("bidirectional_schema", "naive_parallel")]
    hard = runs[("hard_dependency_control", "hard_dependency")]
    case1_producer = _worker_result(case1_live, "producer")
    case1_consumer = _worker_result(case1_live, "consumer")
    case2_producer = _worker_result(case2_live, "producer")
    case2_consumer = _worker_result(case2_live, "consumer")
    hard_producer = _worker_result(hard, "producer")
    hard_consumer = _worker_result(hard, "consumer")

    case2_types = [event.event_type for event in case2_live.handoff_events]
    return {
        "case1_live_starts_before_producer_completes": (
            case1_consumer.started_at_ms < case1_producer.ended_at_ms
        ),
        "case1_live_passes": case1_live.integration_passed,
        "case1_sequential_passes": case1_sequential.integration_passed,
        "case1_naive_exposes_contract_mismatch": not case1_naive.integration_passed,
        "case2_live_starts_before_producer_completes": (
            case2_consumer.started_at_ms < case2_producer.ended_at_ms
        ),
        "case2_ready_feedback_update_observed": case2_types
        == [LiveEventType.READY, LiveEventType.FEEDBACK, LiveEventType.UPDATE],
        "case2_producer_trajectory_changed": bool(
            case2_producer.candidate and case2_producer.candidate.trajectory_changed
        ),
        "case2_consumer_consumed_v2": (
            case2_consumer.consumed_versions.get("producer:config_schema") == 2
        ),
        "case2_live_passes": case2_live.integration_passed,
        "case2_sequential_requires_rework": not case2_sequential.integration_passed,
        "case2_naive_requires_rework": not case2_naive.integration_passed,
        "case3_hard_consumer_waits_for_completion": (
            hard_consumer.started_at_ms >= hard_producer.ended_at_ms
        ),
        "case3_hard_passes": hard.integration_passed,
    }


def run_suite(
    *,
    plan_path: Path,
    output_root: Path,
    stable_baseline_commit: str,
) -> dict[str, Any]:
    plan, cases = _load_frozen_plan(plan_path)
    output_root.mkdir(parents=True, exist_ok=True)
    published_plan_path = output_root / "plan.json"
    if plan_path.resolve() != published_plan_path.resolve():
        atomic_write_json(published_plan_path, plan)

    runs: dict[tuple[str, str], LiveHandoffSummary] = {}
    for scenario, mode in EXPECTED_RUNS:
        runs[(scenario, mode)] = _run_one(
            scenario=scenario,
            mode=mode,
            case=cases[scenario],
            output_root=output_root,
        )

    assertions = _evaluate_assertions(runs)
    plan_digest = hashlib.sha256(published_plan_path.read_bytes()).hexdigest()
    result = {
        "schema_version": 1,
        "experiment_id": plan["experiment_id"],
        "stable_baseline_commit": stable_baseline_commit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "plan_sha256": plan_digest,
        "overall_passed": all(assertions.values()),
        "assertions": assertions,
        "runs": [
            {
                "scenario": scenario,
                "mode": mode,
                "status": summary.status,
                "wall_time_ms": summary.wall_time_ms,
                "integration_passed": summary.integration_passed,
                "metrics": summary.metrics,
                "artifact": f"runs/{summary.run_id}/summary.json",
                "timeline": f"runs/{summary.run_id}/timeline.jsonl",
            }
            for (scenario, mode), summary in runs.items()
        ],
    }
    atomic_write_json(output_root / "result.json", result)
    return result


def _resolve_stable_baseline() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "stable/v0-20260818"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plan",
        type=Path,
        default=CANONICAL_EXPERIMENT_ROOT / "plan.json",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--baseline", default=None)
    args = parser.parse_args()

    result = run_suite(
        plan_path=args.plan.resolve(),
        output_root=args.output.resolve(),
        stable_baseline_commit=args.baseline or _resolve_stable_baseline(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

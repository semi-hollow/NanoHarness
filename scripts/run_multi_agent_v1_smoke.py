#!/usr/bin/env python3
"""运行唯一的 deterministic Multi-Agent V1 机制验证。

系统角色：用一个自然语言任务串起 Adaptive Planner、真实 AgentLoop、隔离
worktree、LIVE Handoff、候选 Diff 集成和 Finalizer。它证明机制确实接入生产
主链，但不评估真实模型性能，也不是第二套 Multi-Agent 实现。

输入是脚本内的 hermetic 小仓库；输出是脱敏 ``mechanism-evidence.json``，原始
Trace 只写独立 ``.agent_forge/runs/v1-multi-agent``。折叠时按“脚本模型 ->
真实运行 -> fixture -> evidence”阅读即可。
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from agent_forge.infrastructure.atomic_json import atomic_write_json
from agent_forge.multi_agent.application.planning import (
    AdaptivePlanner,
    write_planning_artifact,
)
from agent_forge.multi_agent.domain.fanout import (
    FANOUT_MECHANISM_EVIDENCE_SCHEMA_VERSION,
)
from agent_forge.multi_agent.wiring import FanoutBuildRequest, build_fanout
from agent_forge.observability.adapters.json_trace import TraceRecorder
from agent_forge.runtime.adapters.execution_environment import ExecutionEnvironment
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import AgentResponse, Message, ToolCall
from agent_forge.runtime.wiring import ToolRegistryBuildRequest, build_registry
from agent_forge.tools.registry import ToolRegistry


# region 1. 固定实验契约：一个自然语言任务、独立输出路径和 deterministic Planner
PROJECT_ROOT = Path(__file__).resolve().parents[1]
NATURAL_LANGUAGE_TASK = (
    "升级配置加载能力以支持新的 timeout 字段，同时保持现有调用方兼容，"
    "并完成必要实现和验证。请选择最小安全的执行策略。"
)
DEFAULT_RAW_ROOT = PROJECT_ROOT / ".agent_forge" / "runs" / "v1-multi-agent"
DEFAULT_SUMMARY = (
    PROJECT_ROOT
    / "benchmarks"
    / "experiments"
    / "multi-agent-v1"
    / "mechanism-evidence.json"
)
EVENT_ID_PATTERN = re.compile(r'"event_id":\s*"([a-f0-9]{64})"')


class _PlannerModel:
    """只固定计划响应；计划解析和领域校验仍走真实 ``AdaptivePlanner``。"""

    last_usage = None

    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:
        return AgentResponse(
            json.dumps(
                {
                    "mode": "multi",
                    "reason": "two isolated files need one bounded semantic contract",
                    "global_acceptance_criteria": [
                        "legacy and new timeout inputs produce the same normalized value"
                    ],
                    "tasks": [
                        {
                            "id": "producer",
                            "task": "Publish the config contract and implement compatibility feedback.",
                            "depends_on": [],
                            "write_scope": ["config_schema.py"],
                            "allowed_tools": ["replace_text"],
                            "acceptance_criteria": [
                                "normalize_config accepts timeout and legacy_timeout"
                            ],
                            "max_steps": 6,
                        },
                        {
                            "id": "consumer",
                            "task": "Report the legacy constraint and implement the consumer against v2.",
                            "depends_on": [],
                            "write_scope": ["service_consumer.py"],
                            "allowed_tools": ["replace_text"],
                            "acceptance_criteria": [
                                "boot_service consumes the compatible normalized config"
                            ],
                            "max_steps": 6,
                        },
                    ],
                    "live_dependencies": [
                        {
                            "producer_task_id": "producer",
                            "target_task_id": "consumer",
                            "semantic_key": "config_schema",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            [],
        )
# endregion 1. 固定实验契约


# region 2. Scripted Model：控制并发时序，但仍走真实 AgentLoop 与 ToolCall
class _AcceptedEventWaiter:
    """等待 Runtime 接受指定事件，不用 sleep 猜并发顺序。"""

    def __init__(self) -> None:
        self._runtime: Callable[[], Any] | None = None

    def bind(self, runtime: Callable[[], Any]) -> None:
        self._runtime = runtime

    def wait(self, event_type: str, timeout: float = 10.0) -> None:
        if self._runtime is None:
            raise RuntimeError("event waiter is not bound")
        deadline = time.monotonic() + timeout
        runtime = self._runtime()
        revision = runtime.state_revision
        while not any(
            row.get("event", {}).get("event_type") == event_type
            for row in runtime.timeline
        ):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timed out waiting for {event_type}")
            revision = runtime.wait_for_change(revision, remaining)


class _ScriptedModelPort:
    """为两个 Worker 提供可重复响应，并记录真实下一 Turn 的模型输入。"""

    last_usage = None

    def __init__(self, waiter: _AcceptedEventWaiter) -> None:
        self.waiter = waiter
        self.producer_in_flight = threading.Event()
        self.consumer_in_flight = threading.Event()
        self._lock = threading.Lock()
        self.calls = {"producer": 0, "consumer": 0, "finalizer": 0}
        self.inputs: dict[str, list[str]] = {
            "producer": [],
            "consumer": [],
            "finalizer": [],
        }

    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:
        text = "\n".join(message.content for message in messages)
        actor = (
            "finalizer"
            if "FanoutFinalizer" in text
            else "producer"
            if "task_id=producer" in text
            else "consumer"
        )
        with self._lock:
            self.calls[actor] += 1
            call = self.calls[actor]
            self.inputs[actor].append(text)
        if actor == "finalizer":
            return AgentResponse(
                "\n".join(
                    [
                        "CRITERION 1: PASS | integrated behavior validated",
                        "CRITERION 2: PASS | producer candidate integrated",
                        "CRITERION 3: PASS | consumer candidate integrated",
                        "FINAL: PASS",
                    ]
                ),
                [],
            )
        return self._producer(call, text) if actor == "producer" else self._consumer(call, text)

    def _producer(self, call: int, text: str) -> AgentResponse:
        if call == 1:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "producer-ready-v1",
                        "publish_handoff_event",
                        {
                            "event_type": "READY",
                            "target_task_id": "consumer",
                            "semantic_key": "config_schema",
                            "version": 1,
                            "summary": "Schema v1 accepts timeout.",
                            "evidence": ["accepted_keys=timeout"],
                        },
                    )
                ],
            )
        if call == 2:
            self.producer_in_flight.set()
            self.waiter.wait("FEEDBACK")
            return AgentResponse("stale producer response", [])
        if call == 3:
            _require_runtime_evidence(text, "FEEDBACK")
            if not self.consumer_in_flight.wait(10):
                raise TimeoutError("consumer never entered its in-flight model call")
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "producer-edit",
                        "replace_text",
                        {
                            "path": "config_schema.py",
                            "old": "    raw_timeout = config['timeout']\n",
                            "new": (
                                "    raw_timeout = config.get('timeout', "
                                "config.get('legacy_timeout'))\n"
                            ),
                        },
                    ),
                    ToolCall(
                        "producer-update-v2",
                        "publish_handoff_event",
                        {
                            "event_type": "UPDATE",
                            "target_task_id": "consumer",
                            "semantic_key": "config_schema",
                            "version": 2,
                            "summary": "Schema v2 preserves legacy_timeout.",
                            "evidence": ["accepted_keys=timeout,legacy_timeout"],
                            "caused_by_event_id": _latest_event_id(text),
                        },
                    ),
                ],
            )
        return AgentResponse("producer candidate complete", [])

    def _consumer(self, call: int, text: str) -> AgentResponse:
        if call == 1:
            _require_runtime_evidence(text, "READY")
            if not self.producer_in_flight.wait(10):
                raise TimeoutError("producer never entered its in-flight model call")
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "consumer-feedback-v1",
                        "publish_handoff_event",
                        {
                            "event_type": "FEEDBACK",
                            "target_task_id": "producer",
                            "semantic_key": "config_schema",
                            "version": 1,
                            "summary": "Existing callers require legacy_timeout.",
                            "evidence": ["fixture uses legacy_timeout"],
                            "caused_by_event_id": _latest_event_id(text),
                        },
                    )
                ],
            )
        if call == 2:
            self.consumer_in_flight.set()
            self.waiter.wait("UPDATE")
            return AgentResponse("stale consumer response", [])
        if call == 3:
            _require_runtime_evidence(text, "UPDATE")
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "consumer-edit",
                        "replace_text",
                        {
                            "path": "service_consumer.py",
                            "old": "    raise NotImplementedError\n",
                            "new": (
                                "    return normalize_config("
                                "{'legacy_timeout': '30'})\n"
                            ),
                        },
                    )
                ],
            )
        return AgentResponse("consumer candidate complete", [])
# endregion 2. Scripted Model


# region 3. 唯一运行入口：Planner -> composition root -> Coordinator -> evidence
def run_smoke(
    *,
    raw_root: Path | None = None,
    summary_path: Path | None = None,
) -> dict[str, Any]:
    """运行一个 hermetic mechanism case，并写出脱敏事实投影。"""

    # region 3.1 Fixture 与计划：临时 Git 仓库不会覆盖历史 Evidence
    selected_raw_root = raw_root or DEFAULT_RAW_ROOT
    selected_summary = summary_path or DEFAULT_SUMMARY
    with tempfile.TemporaryDirectory(prefix="nanoharness-multi-agent-v1-") as temporary:
        repository = Path(temporary) / "repo"
        repository.mkdir()
        _initialize_repository(repository)
        run_dir = selected_raw_root / "deterministic-smoke-v1"
        run_dir.mkdir(parents=True, exist_ok=True)

        planning = AdaptivePlanner(
            model_factory=_PlannerModel,
            available_tools=["replace_text"],
            max_fanout_tasks=4,
            max_steps=6,
        ).decide(NATURAL_LANGUAGE_TASK, repository)
        if planning.decision is None or planning.decision.mode != "multi":
            raise AssertionError(f"Planner did not select multi: {planning.failure}")
        write_planning_artifact(run_dir / "planning.json", planning)
        plan = planning.decision.to_fanout_plan(NATURAL_LANGUAGE_TASK)
        # endregion 3.1 Fixture 与计划

        # region 3.2 真实主链：唯一 wiring 装配真实 Coordinator 与 AgentLoop
        waiter = _AcceptedEventWaiter()
        model = _ScriptedModelPort(waiter)
        trace = TraceRecorder(str(run_dir / "trace.json"))
        coordinator = build_fanout(
            FanoutBuildRequest(
                plan=plan,
                base_config=RuntimeConfig(
                    workspace=str(repository),
                    max_steps=6,
                    auto_approve_writes=True,
                ),
                trace=trace,
                run_dir=run_dir,
                llm_factory=lambda: model,
                registry_factory=_build_registry,
                max_workers=2,
            )
        )
        waiter.bind(lambda: coordinator.live_handoff)
        summary = coordinator.run()
        trace.write()
        # endregion 3.2 真实主链

        # region 3.3 结果收口：验证集成行为，再发布 sanitized derived evidence
        behavior_passed = _validate_integrated_result(repository)
        projection = _project_evidence(
            planning=planning.to_dict(),
            plan=plan,
            coordinator=coordinator,
            summary=summary,
            model=model,
            behavior_passed=behavior_passed,
            fanout_events=trace.events,
        )
        atomic_write_json(selected_summary, projection)
        return projection
        # endregion 3.3 结果收口
# endregion 3. 唯一运行入口


# region 4. Hermetic fixture：初始化仓库、生产 Tool Registry 和最终行为断言
def _initialize_repository(repository: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "nanoharness@local"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "NanoHarness"],
        cwd=repository,
        check=True,
    )
    (repository / "config_schema.py").write_text(
        "def normalize_config(config):\n"
        "    raw_timeout = config['timeout']\n"
        "    return {'timeout': int(raw_timeout)}\n",
        encoding="utf-8",
    )
    (repository / "service_consumer.py").write_text(
        "def boot_service(normalize_config):\n"
        "    raise NotImplementedError\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "baseline"],
        cwd=repository,
        check=True,
    )


def _build_registry(
    workspace: Path,
    environment: ExecutionEnvironment,
) -> ToolRegistry:
    return build_registry(
        ToolRegistryBuildRequest(
            workspace=str(workspace),
            auto=True,
            enabled_tools=("replace_text", "git_diff", "git_status"),
            execution_environment=environment,
        )
    )


def _validate_integrated_result(repository: Path) -> bool:
    """执行集成后的两个小函数，证明最终 Diff 的业务行为成立。"""

    namespace: dict[str, Any] = {}
    exec((repository / "config_schema.py").read_text(encoding="utf-8"), namespace)
    exec((repository / "service_consumer.py").read_text(encoding="utf-8"), namespace)
    return namespace["boot_service"](namespace["normalize_config"]) == {"timeout": 30}
# endregion 4. Hermetic fixture


# region 5. Evidence 投影：只保留可追问的因果链、版本和集成事实
def _project_evidence(
    *,
    planning: dict[str, Any],
    plan: Any,
    coordinator: Any,
    summary: Any,
    model: _ScriptedModelPort,
    behavior_passed: bool,
    fanout_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """从 canonical Runtime/Trace 事实生成不含私有 Prompt 的机制证据。"""

    # region 5.1 事实提取：事件顺序、Worker 生命周期和 stale response
    runtime = coordinator.live_handoff
    timeline = runtime.timeline
    events = [
        row["event"] for row in timeline if row["record_type"] == "handoff_event"
    ]
    record_order = [
        (row["record_type"], row.get("task_id", "")) for row in timeline
    ]
    producer_started = record_order.index(("worker_attempt_started", "producer"))
    consumer_started = record_order.index(("worker_attempt_started", "consumer"))
    producer_finished = record_order.index(("worker_attempt_finished", "producer"))
    sealed = [
        row["task_id"]
        for row in timeline
        if row["record_type"] == "integration_sealed" and row["success"]
    ]
    traces = [
        json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
        for result in summary.attempt_results
    ]
    stale_discards = [
        event
        for trace in traces
        for event in trace.get("events", [])
        if event.get("event_type") == "recovery_decision"
        and event.get("failure_kind") == "runtime_coordination"
    ]
    candidate_gate_facts = [
        {
            key: event.get(key)
            for key in ("task_id", "attempt", "gate", "decision", "detail")
        }
        for event in fanout_events
        if event.get("event_type") == "candidate_gate"
    ]
    start_event = next(
        event for event in fanout_events if event.get("event_type") == "fanout_start"
    )
    checkpoint = json.loads(
        (coordinator.artifacts.root / "fanout_checkpoint.json").read_text(
            encoding="utf-8"
        )
    )
    # endregion 5.1 事实提取

    # region 5.2 机制断言：每条结论都绑定真实 Timeline、输入或集成结果
    assertions = {
        "planner_selected_multi": planning["decision"]["mode"] == "multi",
        "plan_is_deeply_immutable": (
            isinstance(plan.tasks, tuple)
            and isinstance(plan.tasks[0].write_scope, tuple)
            and isinstance(plan.live_dependencies, tuple)
        ),
        "plan_digest_stable": (
            start_event.get("plan_digest")
            == plan.digest
            == summary.plan_digest
            == checkpoint.get("plan_digest")
        ),
        "isolated_worker_worktrees": len(
            {result.workspace for result in summary.attempt_results}
        )
        == 2,
        "consumer_started_before_producer_completed": (
            producer_started < consumer_started < producer_finished
        ),
        "ready_feedback_update_observed": [event["event_type"] for event in events]
        == ["READY", "FEEDBACK", "UPDATE"],
        "feedback_entered_producer_next_model_input": any(
            "RUNTIME COORDINATION EVIDENCE" in item and '"event_type": "FEEDBACK"' in item
            for item in model.inputs["producer"]
        ),
        "update_v2_caused_by_feedback_event": (
            len(events) == 3
            and events[2]["caused_by_event_id"] == events[1]["event_id"]
        ),
        "consumer_consumed_latest_version": runtime.consumed_versions("consumer")
        == {"producer:config_schema": 2},
        "consumer_waited_for_producer_final_integration": sealed == [
            "producer",
            "consumer",
        ],
        "integration_and_finalizer_passed": (
            summary.status == "passed"
            and summary.final_decision == "PASS"
            and behavior_passed
        ),
        "stale_model_response_discarded": len(stale_discards) >= 2,
        "stale_response_uses_refresh_input": all(
            event.get("model_step_outcome") == "refresh_input"
            for event in stale_discards
        ),
        "strict_frontier_integrated_prefix": summary.merged_task_ids
        == ["producer", "consumer"],
        "candidate_gates_observed": (
            "candidate_local_validation" in {row["gate"] for row in candidate_gate_facts}
            and "trusted_commit" in {row["gate"] for row in candidate_gate_facts}
        ),
    }
    if not all(assertions.values()):
        raise AssertionError(f"Multi-Agent V1 smoke failed: {assertions}")
    # endregion 5.2 机制断言

    # region 5.3 脱敏输出：区分 Reused、New、Omitted 和性能 Claim Boundary
    return {
        "schema_version": FANOUT_MECHANISM_EVIDENCE_SCHEMA_VERSION,
        "evidence_class": "deterministic_real_agent_loop_mechanism",
        "natural_language_task": NATURAL_LANGUAGE_TASK,
        "planning_decision": planning["decision"],
        "fanout_plan": plan.to_dict(),
        "plan_digest": plan.digest,
        "status": summary.status,
        "launch_waves": summary.launch_waves,
        "task_results": [
            _public_task_result(result) for result in summary.task_results
        ],
        "attempt_results": [
            _public_attempt_result(result) for result in summary.attempt_results
        ],
        "coordination_timeline": [
            {
                "sequence": row["sequence"],
                "record_type": row["record_type"],
                "task_id": row.get("task_id", ""),
                "boundary": row.get("boundary", ""),
                "event": (
                    {
                        key: row["event"].get(key)
                        for key in (
                            "event_id",
                            "event_type",
                            "publisher_task_id",
                            "target_task_id",
                            "semantic_key",
                            "version",
                            "plan_digest",
                            "worker_attempt_id",
                            "caused_by_event_id",
                        )
                    }
                    if isinstance(row.get("event"), dict)
                    else None
                ),
                "human_authority": row.get("human_authority"),
            }
            for row in timeline
        ],
        "candidate_gate_facts": candidate_gate_facts,
        "finalizer_evidence": {
            "decision": summary.final_decision,
            "criterion_results": [
                result.to_dict() for result in summary.criterion_results
            ],
            "read_only": True,
        },
        "assertions": assertions,
        "reused": [
            "AdaptivePlanner",
            "AgentLoop",
            "RunControl safe boundary",
            "isolated Git worktree",
            "candidate diff integration gates",
            "criteria-aware Finalizer",
        ],
        "new": [
            "FanoutPlan.live_dependencies",
            "LiveHandoffRuntime",
            "publish_handoff_event",
            "final LIVE freshness barrier",
        ],
        "omitted": [
            "real-model benchmark",
            "performance comparison",
            "LIVE resume/replay",
            "distributed transport",
        ],
        "real_model_performance_evaluated": False,
        "benchmark_claim": "none",
    }
    # endregion 5.3 脱敏输出
# endregion 5. Evidence 投影


# region 6. 因果辅助断言与脚本启动
def _public_attempt_result(result: Any) -> dict[str, Any]:
    """保留 current schema，但把本机临时绝对路径投影为稳定 evidence 路径。"""

    payload = result.to_dict()
    prefix = f"workers/{result.task_id}/attempt-{result.attempt}"
    replacements = {
        "candidate_diff_path": f"{prefix}/candidate_changes.diff",
        "artifact_path": f"{prefix}/task_output.md",
        "trace_path": f"{prefix}/trace.json",
        "usage_path": f"{prefix}/usage.json",
        "environment_manifest_path": f"{prefix}/execution_environment.json",
        "workspace": f"isolated-worktree:{result.task_id}:attempt-{result.attempt}",
    }
    for key, value in replacements.items():
        if payload.get(key):
            payload[key] = value
    handoff = payload.get("handoff")
    if isinstance(handoff, dict) and handoff.get("artifact_path"):
        handoff["artifact_path"] = replacements["artifact_path"]
    return payload


def _public_task_result(result: Any) -> dict[str, Any]:
    payload = result.to_dict()
    handoff = payload.get("handoff")
    if isinstance(handoff, dict) and handoff.get("artifact_path"):
        attempt = result.final_attempt or 0
        handoff["artifact_path"] = (
            f"workers/{result.task_id}/attempt-{attempt}/task_output.md"
        )
    return payload


def _require_runtime_evidence(text: str, event_type: str) -> None:
    if (
        "RUNTIME COORDINATION EVIDENCE" not in text
        or "human_authority=false" not in text
        or f'"event_type": "{event_type}"' not in text
    ):
        raise AssertionError(f"missing Runtime {event_type} evidence in model input")


def _latest_event_id(text: str) -> str:
    matches = EVENT_ID_PATTERN.findall(text)
    if not matches:
        raise AssertionError("coordination input contains no event_id")
    return matches[-1]


if __name__ == "__main__":
    result = run_smoke()
    print(json.dumps(result["assertions"], ensure_ascii=False, indent=2))
# endregion 6. 因果辅助断言与脚本启动

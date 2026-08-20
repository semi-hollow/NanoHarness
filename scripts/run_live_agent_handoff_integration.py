#!/usr/bin/env python3
"""Run one hermetic real-AgentLoop Live Handoff integration case."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path
from threading import Event
from typing import Any, Callable

from agent_forge.atomic_json import atomic_write_json
from agent_forge.multi_agent.adapters.git_workspace import GitFanoutWorkspace
from agent_forge.multi_agent.adapters.live_agent_worker import (
    LiveCandidateDiffIntegration,
)
from agent_forge.multi_agent.api import (
    DependencyType,
    LiveAgentHandoffBuildRequest,
    LiveDependency,
    LiveEventType,
    LiveHandoffPlan,
    SubagentTask,
    build_live_agent_handoff,
)
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import AgentResponse, Message, ToolCall
from agent_forge.runtime.execution_environment import ExecutionEnvironment
from agent_forge.runtime.wiring import ToolRegistryBuildRequest, build_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / ".agent_forge"
    / "runs"
    / "live-handoff-v1"
    / "real-agent-loop-integration.json"
)
EVENT_ID_PATTERN = re.compile(r'"event_id":\s*"([a-f0-9]{64})"')


class IntegrationSynchronization:
    """只稳定并发先后，不代替 Runtime 事件或 AgentLoop 边界。"""

    def __init__(self) -> None:
        self.producer_model_in_flight = Event()
        self.consumer_model_in_flight = Event()


class AcceptedEventWaiter:
    """测试 Adapter 从 Runtime 事实流等待指定 accepted event。"""

    def __init__(self) -> None:
        self.runtime_provider: Callable[[], Any] | None = None

    def bind(self, runtime_provider: Callable[[], Any]) -> None:
        self.runtime_provider = runtime_provider

    def wait(self, event_type: LiveEventType, timeout: float = 5.0) -> None:
        if self.runtime_provider is None:
            raise RuntimeError("accepted event waiter is not bound")
        runtime = self.runtime_provider()
        generation = runtime.generation
        remaining = timeout
        while not any(event.event_type == event_type for event in runtime.events):
            if remaining <= 0:
                raise TimeoutError(f"timed out waiting for accepted {event_type.value}")
            interval = min(0.1, remaining)
            generation = runtime.wait_for_change(generation, interval)
            remaining -= interval


class ProducerScriptedModel:
    """READY 后故意保持一次真实 model request 在途，等待 FEEDBACK。"""

    last_usage = None

    def __init__(
        self,
        synchronization: IntegrationSynchronization,
        event_waiter: AcceptedEventWaiter,
    ) -> None:
        self.synchronization = synchronization
        self.event_waiter = event_waiter
        self.calls = 0
        self.inputs: list[list[Message]] = []

    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:
        self.calls += 1
        self.inputs.append(list(messages))
        tool_names = {str(tool.get("name") or "") for tool in tools}
        if self.calls == 1:
            if "publish_handoff_event" not in tool_names:
                raise AssertionError("producer cannot see publish_handoff_event")
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
                            "summary": "Schema v1 accepts timeout only.",
                            "evidence": ["accepted_keys=timeout"],
                        },
                    )
                ],
            )
        if self.calls == 2:
            self.synchronization.producer_model_in_flight.set()
            self.event_waiter.wait(LiveEventType.FEEDBACK)
            return AgentResponse("stale producer response before feedback", [])
        if self.calls == 3:
            prompt = _message_text(messages)
            _assert_coordination_prompt(prompt, "FEEDBACK")
            feedback_event_id = _latest_event_id(prompt)
            if not self.synchronization.consumer_model_in_flight.wait(timeout=5):
                raise TimeoutError("consumer did not enter its in-flight model request")
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "producer-revise-schema",
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
                            "summary": "Schema v2 accepts legacy_timeout.",
                            "evidence": [
                                "accepted_keys=timeout,legacy_timeout",
                                "candidate=config_schema.py",
                            ],
                            "caused_by_event_id": feedback_event_id,
                        },
                    ),
                ],
            )
        return AgentResponse("completed producer schema v2 candidate", [])


class ConsumerScriptedModel:
    """消费 READY，发布 FEEDBACK，并在在途请求后消费 UPDATE。"""

    last_usage = None

    def __init__(
        self,
        synchronization: IntegrationSynchronization,
        event_waiter: AcceptedEventWaiter,
    ) -> None:
        self.synchronization = synchronization
        self.event_waiter = event_waiter
        self.calls = 0
        self.inputs: list[list[Message]] = []

    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:
        self.calls += 1
        self.inputs.append(list(messages))
        prompt = _message_text(messages)
        if self.calls == 1:
            _assert_coordination_prompt(prompt, "READY")
            ready_event_id = _latest_event_id(prompt)
            if not self.synchronization.producer_model_in_flight.wait(timeout=5):
                raise TimeoutError("producer did not enter its in-flight model request")
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
                            "summary": "Deployed configs still require legacy_timeout.",
                            "evidence": ["fixture uses legacy_timeout"],
                            "severity": "blocking",
                            "caused_by_event_id": ready_event_id,
                        },
                    )
                ],
            )
        if self.calls == 2:
            self.synchronization.consumer_model_in_flight.set()
            self.event_waiter.wait(LiveEventType.UPDATE)
            return AgentResponse("stale consumer response before update", [])
        if self.calls == 3:
            _assert_coordination_prompt(prompt, "UPDATE")
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "consumer-implement-candidate",
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
        return AgentResponse("completed consumer candidate against schema v2", [])


def run_real_agent_loop_case(output_path: Path | None = None) -> dict[str, Any]:
    """执行真实 AgentLoop Case，并只发布去路径化的派生证据。"""

    with tempfile.TemporaryDirectory(prefix="nanoharness-live-agent-") as temporary:
        root = Path(temporary)
        repository = root / "repo"
        repository.mkdir()
        _initialize_repository(repository)
        run_root = root / "run"
        synchronization = IntegrationSynchronization()
        event_waiter = AcceptedEventWaiter()
        created_models: dict[str, Any] = {}

        def model_factory(task: SubagentTask):
            if task.id == "producer":
                model = ProducerScriptedModel(synchronization, event_waiter)
            else:
                model = ConsumerScriptedModel(synchronization, event_waiter)
            created_models[task.id] = model
            return model

        plan = _real_plan()
        integration = LiveCandidateDiffIntegration(
            workspace=repository,
            workspace_port=GitFanoutWorkspace(repository),
            task_order=("producer", "consumer"),
            validator=_validate_integrated_candidate,
        )
        coordinator = build_live_agent_handoff(
            LiveAgentHandoffBuildRequest(
                plan=plan,
                scenario="bidirectional_schema_real_agent_loop",
                mode="live_handoff",
                base_config=RuntimeConfig(
                    workspace=str(repository),
                    max_steps=6,
                    auto_approve_writes=True,
                ),
                run_dir=run_root,
                model_factory=model_factory,
                registry_factory=_build_registry,
                integration=integration,
                max_workers=2,
                timeout_seconds=15,
                run_id="real-agent-loop-live-handoff",
            )
        )
        event_waiter.bind(lambda: coordinator.runtime)
        summary = coordinator.run()
        projection = _project_evidence(summary, created_models)
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(output_path, projection)
        return projection


def _real_plan() -> LiveHandoffPlan:
    return LiveHandoffPlan(
        goal="Adapt a config producer and consumer through in-flight schema feedback.",
        tasks=(
            SubagentTask(
                id="producer",
                task="Publish schema readiness, then implement validated feedback.",
                write_scope=["config_schema.py"],
                allowed_tools=["replace_text", "publish_handoff_event"],
                expected_artifact="producer_candidate",
                max_steps=6,
            ),
            SubagentTask(
                id="consumer",
                task="Report the legacy constraint and implement against schema v2.",
                write_scope=["service_consumer.py"],
                allowed_tools=["replace_text", "publish_handoff_event"],
                expected_artifact="consumer_candidate",
                max_steps=6,
            ),
        ),
        dependencies=(
            LiveDependency(
                producer_task_id="producer",
                target_task_id="consumer",
                dependency_type=DependencyType.LIVE,
                semantic_key="config_schema",
            ),
        ),
    )


def _initialize_repository(repository: Path) -> None:
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "agent-forge@local"],
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
        ["git", "commit", "-m", "baseline"],
        cwd=repository,
        check=True,
        capture_output=True,
    )


def _build_registry(
    workspace: Path,
    environment: ExecutionEnvironment,
):
    return build_registry(
        ToolRegistryBuildRequest(
            workspace=str(workspace),
            auto=True,
            execution_environment=environment,
        )
    )


def _validate_integrated_candidate(workspace: Path) -> tuple[bool, str]:
    namespace: dict[str, Any] = {}
    producer = (workspace / "config_schema.py").read_text(encoding="utf-8")
    consumer = (workspace / "service_consumer.py").read_text(encoding="utf-8")
    try:
        exec(compile(producer, "config_schema.py", "exec"), namespace)
        exec(compile(consumer, "service_consumer.py", "exec"), namespace)
        actual = namespace["boot_service"](namespace["normalize_config"])
    except Exception as exc:
        return False, f"integrated candidate raised {type(exc).__name__}: {exc}"
    if actual != {"timeout": 30}:
        return False, f"unexpected integrated result: {actual!r}"
    return True, "real worktree candidate diffs integrated and assertion passed"


def _project_evidence(summary: Any, models: dict[str, Any]) -> dict[str, Any]:
    events = [event.to_dict() for event in summary.handoff_events]
    event_ids = {
        event["event_type"]: event["event_id"]
        for event in events
    }
    runtime_boundaries = [
        {
            "sequence": record["sequence"],
            "record_type": record["record_type"],
            "task_id": record.get("task_id", ""),
            "boundary": record.get("boundary", ""),
            "event_ids": [event["event_id"] for event in record.get("events", [])],
        }
        for record in summary.timeline
        if record["record_type"] == "mailbox_drained" and record.get("events")
    ]
    worker_evidence: dict[str, Any] = {}
    for result in summary.results:
        trace = json.loads(
            Path(result.candidate.payload["trace_path"]).read_text(encoding="utf-8")
        )
        selected_trace = []
        coordination_operation_ledger_events = 0
        for event in trace.get("events", []):
            event_type = event.get("event_type")
            if event_type == "runtime_coordination":
                selected_trace.append(
                    {
                        "step": event.get("step"),
                        "event_type": event_type,
                        "boundary": event.get("boundary"),
                        "human_authority": event.get("human_authority"),
                        "provenance": (event.get("coordination") or {}).get(
                            "provenance"
                        ),
                    }
                )
            elif event_type == "recovery_decision" and event.get(
                "failure_kind"
            ) == "runtime_input_changed":
                selected_trace.append(
                    {
                        "step": event.get("step"),
                        "event_type": event_type,
                        "failure_kind": event.get("failure_kind"),
                        "input_sources": event.get("input_sources"),
                    }
                )
            elif event_type in {"tool_call", "tool_observation"} and (
                event.get("tool_name") == "publish_handoff_event"
                or "publish_handoff_event" in json.dumps(event, ensure_ascii=False)
            ):
                selected_trace.append(
                    {
                        "step": event.get("step"),
                        "event_type": event_type,
                        "tool_name": event.get("tool_call"),
                        "success": event.get("success"),
                    }
                )
            elif event_type == "permission_check" and event.get(
                "tool_call"
            ) == "publish_handoff_event":
                selected_trace.append(
                    {
                        "step": event.get("step"),
                        "event_type": event_type,
                        "tool_name": event.get("tool_call"),
                        "permission_action": event.get("permission_action"),
                        "permission_decision": event.get("permission_decision"),
                    }
                )
            elif event_type == "operation_ledger" and "publish_handoff_event" in json.dumps(
                event,
                ensure_ascii=False,
            ):
                coordination_operation_ledger_events += 1
        worker_evidence[result.task_id] = {
            "model_calls": models[result.task_id].calls,
            "touched_files": result.candidate.payload["touched_files"],
            "candidate_diff_sha256": result.candidate.payload[
                "candidate_diff_sha256"
            ],
            "attempts": [
                attempt.to_dict() for attempt in result.candidate.attempts
            ],
            "selected_trace": selected_trace,
            "coordination_operation_ledger_events": (
                coordination_operation_ledger_events
            ),
        }
    assertions = {
        "integration_passed": summary.integration_passed,
        "ready_feedback_update": [event["event_type"] for event in events]
        == ["READY", "FEEDBACK", "UPDATE"],
        "feedback_caused_by_ready": events[1]["caused_by_event_id"]
        == event_ids.get("READY"),
        "update_caused_by_feedback": events[2]["caused_by_event_id"]
        == event_ids.get("FEEDBACK"),
        "both_workers_replanned_from_runtime_coordination": all(
            any(
                item.get("failure_kind") == "runtime_input_changed"
                for item in worker["selected_trace"]
            )
            for worker in worker_evidence.values()
        ),
        "coordination_is_not_human_authority": all(
            item.get("human_authority") is False
            for worker in worker_evidence.values()
            for item in worker["selected_trace"]
            if item["event_type"] == "runtime_coordination"
        ),
        "coordination_publish_is_explicitly_authorized": all(
            item.get("permission_action") == "coordination_publish"
            and item.get("permission_decision") == "allow"
            for worker in worker_evidence.values()
            for item in worker["selected_trace"]
            if item["event_type"] == "permission_check"
        )
        and all(
            any(
                item["event_type"] == "permission_check"
                for item in worker["selected_trace"]
            )
            for worker in worker_evidence.values()
        ),
        "coordination_publish_is_not_operation_ledger_side_effect": all(
            worker["coordination_operation_ledger_events"] == 0
            for worker in worker_evidence.values()
        ),
        "real_candidate_diffs_exist": all(
            bool(worker["candidate_diff_sha256"])
            and bool(worker["touched_files"])
            for worker in worker_evidence.values()
        ),
    }
    return {
        "schema_version": 1,
        "evidence_class": "deterministic_real_agent_loop_integration",
        "performance_evaluated": False,
        "status": summary.status,
        "integration_passed": summary.integration_passed,
        "integration_detail": summary.integration_detail,
        "plan_digest": summary.plan_digest,
        "handoff_events": [
            {
                key: value
                for key, value in event.items()
                if key != "emitted_at"
            }
            for event in events
        ],
        "agent_loop_boundaries": runtime_boundaries,
        "workers": worker_evidence,
        "assertions": assertions,
        "overall_passed": all(assertions.values()),
        "limitations": [
            "Scripted ModelPort; provider/model quality and performance were not evaluated.",
            "Coordination evidence is process-local and has no replay/resume contract.",
            "Stale candidates are detected and rejected; automatic rerun is not implemented.",
        ],
    }


def _message_text(messages: list[Message]) -> str:
    return "\n".join(message.content or "" for message in messages)


def _latest_event_id(prompt: str) -> str:
    matches = EVENT_ID_PATTERN.findall(prompt)
    if not matches:
        raise AssertionError("coordination prompt has no event_id provenance")
    return matches[-1]


def _assert_coordination_prompt(prompt: str, event_type: str) -> None:
    if "[RUNTIME COORDINATION EVIDENCE]" not in prompt:
        raise AssertionError("real AgentLoop did not receive Runtime coordination")
    if f"event_type={event_type}" not in prompt:
        raise AssertionError(f"real AgentLoop did not receive {event_type}")
    if "Operator steer for the current task" in prompt:
        raise AssertionError("peer coordination was rendered as operator steer")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run_real_agent_loop_case(args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

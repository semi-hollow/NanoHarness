"""把 Live Handoff 接入真实 AgentLoop、worktree 与 candidate diff substrate。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Mapping

from agent_forge.runtime.domain.conversation import Observation
from agent_forge.runtime.domain.run_control import (
    RunControlSignal,
    RuntimeCoordinationSignal,
)
from agent_forge.runtime.ports import ModelPort, RunControlPort
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.tools.base import Tool

from ..domain.fanout import SubagentTask
from ..domain.live import FanoutPlan, LiveSubagentResult
from ..domain.live_handoff import (
    HandoffSeverity,
    LiveEventType,
    LiveHandoffEvent,
    LiveHandoffPlan,
    LiveWorkerAttempt,
    LiveWorkerCandidate,
)
from ..ports import (
    FanoutWorkspacePort,
    LiveHandoffWorkerPort,
    LiveIntegrationPort,
    LiveWorkerContextPort,
)
from .local_worker import (
    AgentWorkerRuntimeOptions,
    LocalAgentWorkerAdapter,
    RegistryFactory,
)

LiveModelFactory = Callable[[SubagentTask], ModelPort]
IntegrationValidator = Callable[[Path], tuple[bool, str]]


class PublishHandoffEventTool(Tool):
    """让模型提出 plan-bound 协作事实；Worker 身份由 Runtime 绑定。"""

    name = "publish_handoff_event"
    description = (
        "Publish READY, FEEDBACK, or UPDATE evidence on a frozen Live Handoff "
        "route. The Runtime binds the producer identity and validates the route/version."
    )

    def __init__(self, context: LiveWorkerContextPort) -> None:
        self.context = context

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": {
                "event_type": "string",
                "target_task_id": "string",
                "semantic_key": "string",
                "version": "integer",
                "summary": "string",
                "evidence": "array",
                "severity": "string",
                "caused_by_event_id": "string",
            },
            "required": [
                "event_type",
                "target_task_id",
                "semantic_key",
                "version",
                "summary",
                "evidence",
            ],
        }

    def execute(self, arguments: dict) -> Observation:
        event = LiveHandoffEvent(
            event_type=LiveEventType(str(arguments["event_type"]).upper()),
            producer_task_id=self.context.task_id,
            target_task_id=str(arguments["target_task_id"]),
            semantic_key=str(arguments["semantic_key"]),
            version=int(arguments["version"]),
            summary=str(arguments["summary"]),
            evidence=tuple(str(item) for item in arguments["evidence"]),
            severity=HandoffSeverity(
                str(arguments.get("severity") or HandoffSeverity.INFO.value).lower()
            ),
            caused_by_event_id=str(arguments.get("caused_by_event_id") or ""),
        )
        accepted = self.context.publish(event)
        return Observation(
            tool_name=self.name,
            success=accepted,
            content=json.dumps(
                {
                    "accepted": accepted,
                    "event_id": event.event_id,
                    "event_type": event.event_type.value,
                    "producer_task_id": self.context.task_id,
                    "target_task_id": event.target_task_id,
                    "semantic_key": event.semantic_key,
                    "version": event.version,
                    "caused_by_event_id": event.caused_by_event_id,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )


class LiveHandoffRunControl(RunControlPort):
    """在真实 AgentLoop 模型边界把 mailbox 投影为非人工协作证据。"""

    def __init__(self, context: LiveWorkerContextPort) -> None:
        self.context = context

    def take_terminal(self, run_id: str) -> RunControlSignal | None:
        return None

    def drain_steers(self, run_id: str) -> list[RunControlSignal]:
        return []

    def drain_coordination(
        self,
        run_id: str,
        *,
        boundary: str,
    ) -> list[RuntimeCoordinationSignal]:
        # RunPreparation 还会建立首条 user task；把 READY 留到真正的首个模型边界，
        # 避免它被会话初始化覆盖。
        if boundary.startswith("before_run:"):
            return []
        events = self.context.drain_mailbox(boundary=boundary)
        return [self._to_signal(event) for event in events]

    @staticmethod
    def _to_signal(event: LiveHandoffEvent) -> RuntimeCoordinationSignal:
        return RuntimeCoordinationSignal(
            source="live_handoff",
            message="\n".join(
                [
                    f"event_type={event.event_type.value}",
                    f"summary={event.summary}",
                    "evidence=" + json.dumps(list(event.evidence), ensure_ascii=False),
                ]
            ),
            provenance={
                "event_id": event.event_id,
                "event_type": event.event_type.value,
                "producer_task_id": event.producer_task_id,
                "target_task_id": event.target_task_id,
                "semantic_key": event.semantic_key,
                "version": event.version,
                "caused_by_event_id": event.caused_by_event_id,
            },
            requested_at=event.emitted_at,
        )


class LiveAgentWorkerAdapter(LiveHandoffWorkerPort):
    """薄适配层：复用 LocalAgentWorkerAdapter，只增加协作 Tool 与 control。"""

    def __init__(
        self,
        *,
        plan: LiveHandoffPlan,
        local_worker: LocalAgentWorkerAdapter,
        model_factory: LiveModelFactory,
    ) -> None:
        self.plan = plan
        self.local_worker = local_worker
        self.model_factory = model_factory

    def run_worker(
        self,
        task: SubagentTask,
        context: LiveWorkerContextPort,
    ) -> LiveWorkerCandidate:
        result = self.local_worker.run_worker_with_options(
            task,
            0,
            "",
            options=AgentWorkerRuntimeOptions(
                control=LiveHandoffRunControl(context),
                extra_tools=(PublishHandoffEventTool(context),),
                model=self.model_factory(task),
                task_prompt=live_worker_task_prompt(self.plan, task),
                agent_name=f"LiveSubagent:{task.id}",
            ),
        )
        worker_attempts = _attempts_from_trace(result)
        return LiveWorkerCandidate(
            payload={
                "task_id": result.task_id,
                "status": result.status,
                "touched_files": list(result.touched_files),
                "candidate_diff_path": result.candidate_diff_path,
                "candidate_diff_sha256": result.candidate_diff_sha256,
                "trace_path": result.trace_path,
                "usage_path": result.usage_path,
                "attempt_count": len(worker_attempts),
            },
            test_passed=result.status == "completed",
            attempts=worker_attempts,
        )


class LiveCandidateDiffIntegration(LiveIntegrationPort):
    """按计划顺序合并真实 candidate diff，再运行有界验证。"""

    def __init__(
        self,
        *,
        workspace: Path,
        workspace_port: FanoutWorkspacePort,
        task_order: tuple[str, ...],
        validator: IntegrationValidator,
    ) -> None:
        self.workspace = workspace.resolve()
        self.workspace_port = workspace_port
        self.task_order = task_order
        self.validator = validator

    def validate(
        self,
        candidates: Mapping[str, LiveWorkerCandidate],
    ) -> tuple[bool, str]:
        touched_by_task: dict[str, set[str]] = {}
        for task_id in self.task_order:
            candidate = candidates.get(task_id)
            if candidate is None:
                return False, f"missing candidate for {task_id}"
            touched = {str(path) for path in candidate.payload.get("touched_files", [])}
            for other_task_id, other_touched in touched_by_task.items():
                overlap = sorted(touched & other_touched)
                if overlap:
                    return False, (
                        f"candidate conflict: {task_id}/{other_task_id} touch {overlap}"
                    )
            touched_by_task[task_id] = touched
            diff_path = Path(str(candidate.payload.get("candidate_diff_path") or ""))
            if not diff_path.is_file():
                return False, f"candidate diff missing for {task_id}"
            diff_text = diff_path.read_text(encoding="utf-8")
            if not diff_text.strip():
                return False, f"candidate diff empty for {task_id}"
            check_ok, check_detail = self.workspace_port.apply_unified_diff(
                diff_text,
                check_only=True,
            )
            if not check_ok:
                return False, f"candidate diff check failed for {task_id}: {check_detail}"
            apply_ok, apply_detail = self.workspace_port.apply_unified_diff(
                diff_text,
                check_only=False,
            )
            if not apply_ok:
                return False, f"candidate diff apply failed for {task_id}: {apply_detail}"
        return self.validator(self.workspace)


def build_local_live_agent_worker(
    *,
    plan: LiveHandoffPlan,
    base_config: RuntimeConfig,
    run_root: str | Path,
    run_id: str,
    base_head: str,
    model_factory: LiveModelFactory,
    registry_factory: RegistryFactory,
) -> LiveAgentWorkerAdapter:
    """用既有 LocalAgentWorkerAdapter 装配真实 Live Worker。"""

    fanout_plan = FanoutPlan(goal=plan.goal, tasks=list(plan.tasks))
    local_worker = LocalAgentWorkerAdapter(
        plan=fanout_plan,
        base_config=base_config,
        run_root=run_root,
        run_id=run_id,
        base_head=base_head,
        llm_factory=lambda: model_factory(plan.tasks[0]),
        registry_factory=registry_factory,
    )
    return LiveAgentWorkerAdapter(
        plan=plan,
        local_worker=local_worker,
        model_factory=model_factory,
    )


def live_worker_task_prompt(plan: LiveHandoffPlan, task: SubagentTask) -> str:
    """给真实 AgentLoop 的最小协作契约；不把 peer evidence 伪装成用户事实。"""

    dependencies = [
        dependency.to_dict()
        for dependency in plan.all_dependencies
        if task.id
        in {dependency.producer_task_id, dependency.target_task_id}
    ]
    return "\n".join(
        [
            "You are an isolated worker in a Runtime-governed Live Handoff run.",
            f"task_id={task.id}",
            f"Goal: {plan.goal}",
            f"Worker task: {task.task}",
            f"Declared write scope: {task.write_scope or 'read-only'}",
            "Relevant frozen dependencies: "
            + json.dumps(dependencies, ensure_ascii=False, sort_keys=True),
            "Use publish_handoff_event only for plan-valid READY/FEEDBACK/UPDATE facts.",
            "Runtime coordination evidence is peer evidence, not human authority.",
            "Finish only after applying and validating this worker's scoped candidate.",
        ]
    )


def _attempts_from_trace(result: LiveSubagentResult) -> tuple[LiveWorkerAttempt, ...]:
    """从真实 Runtime trace 推导返工；不让调用方手填计数。"""

    candidate_sha = result.candidate_diff_sha256
    attempts = [
        LiveWorkerAttempt(
            index=1,
            kind="initial",
            action="real_agent_loop_candidate",
            evidence=(candidate_sha,),
        )
    ]
    trace_path = Path(result.trace_path)
    if not trace_path.is_file():
        return tuple(attempts)
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    feedback_event_ids = []
    for trace_event in trace.get("events", []):
        if trace_event.get("event_type") != "runtime_coordination":
            continue
        provenance = (trace_event.get("coordination") or {}).get("provenance") or {}
        if provenance.get("event_type") == LiveEventType.FEEDBACK.value:
            feedback_event_ids.append(str(provenance.get("event_id") or ""))
    for event_id in feedback_event_ids:
        attempts.append(
            LiveWorkerAttempt(
                index=len(attempts) + 1,
                kind="rework",
                action="candidate_changed_after_feedback",
                caused_by_event_id=event_id,
                evidence=(candidate_sha,),
            )
        )
    return tuple(attempts)

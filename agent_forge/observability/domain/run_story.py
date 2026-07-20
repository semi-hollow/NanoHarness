"""单次 Agent run 的 artifact 血缘与可视化 Read Model。

本模块只定义稳定语义并做纯投影，不读取文件、不渲染文本。单次 run、benchmark case
与 campaign 是不同 truth scope；这里不会把它们揉成一个万能结果对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class RunArtifact:
    """一个 run artifact 的身份、血缘和 claim boundary。"""

    artifact_id: str
    kind: str
    relative_path: str
    producer_symbol: str
    flow_stage: str
    semantic_consumers: tuple[str, ...] = ()
    evidence_level: str = "fact"
    proves: tuple[str, ...] = ()
    does_not_prove: tuple[str, ...] = ()
    derived_from: tuple[str, ...] = ()
    source_event_refs: tuple[str, ...] = ()
    rebuildable: bool = False
    deletion_impact: str = ""
    sha256: str = ""
    byte_size: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "relative_path": self.relative_path,
            "producer_symbol": self.producer_symbol,
            "flow_stage": self.flow_stage,
            "semantic_consumers": list(self.semantic_consumers),
            "evidence_level": self.evidence_level,
            "proves": list(self.proves),
            "does_not_prove": list(self.does_not_prove),
            "derived_from": list(self.derived_from),
            "source_event_refs": list(self.source_event_refs),
            "rebuildable": self.rebuildable,
            "deletion_impact": self.deletion_impact,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunArtifact":
        return cls(
            artifact_id=str(value.get("artifact_id") or ""),
            kind=str(value.get("kind") or "unknown"),
            relative_path=str(value.get("relative_path") or ""),
            producer_symbol=str(value.get("producer_symbol") or "unknown"),
            flow_stage=str(value.get("flow_stage") or "unknown"),
            semantic_consumers=_strings(value.get("semantic_consumers")),
            evidence_level=str(value.get("evidence_level") or "unknown"),
            proves=_strings(value.get("proves")),
            does_not_prove=_strings(value.get("does_not_prove")),
            derived_from=_strings(value.get("derived_from")),
            source_event_refs=_strings(value.get("source_event_refs")),
            rebuildable=bool(value.get("rebuildable", False)),
            deletion_impact=str(value.get("deletion_impact") or ""),
            sha256=str(value.get("sha256") or ""),
            byte_size=int(value.get("byte_size") or 0),
        )


@dataclass(frozen=True)
class RunManifest:
    """Single-Run artifact 的唯一机器可读目录。"""

    run_id: str
    task: str
    status: str
    stop_reason: str
    artifacts: tuple[RunArtifact, ...]
    schema_version: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "task": self.task,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunManifest":
        raw_artifacts = value.get("artifacts")
        artifacts = (
            tuple(
                RunArtifact.from_dict(item)
                for item in raw_artifacts
                if isinstance(item, Mapping)
            )
            if isinstance(raw_artifacts, list)
            else ()
        )
        return cls(
            schema_version=int(value.get("schema_version") or 1),
            run_id=str(value.get("run_id") or ""),
            task=str(value.get("task") or ""),
            status=str(value.get("status") or "unknown"),
            stop_reason=str(value.get("stop_reason") or ""),
            artifacts=artifacts,
        )


@dataclass(frozen=True)
class RunStoryStage:
    """黄金主链中的一个语义节点及当前 run 的实际覆盖情况。"""

    stage_id: str
    title: str
    owner_symbol: str
    canonical_upstream: str
    invariant: str
    observed: bool
    event_count: int = 0
    artifact_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunStory:
    """供 CLI、Workbench 和报告共同消费的单次运行解释模型。"""

    run_id: str
    task: str
    status: str
    stop_reason: str
    stages: tuple[RunStoryStage, ...]
    artifacts: tuple[RunArtifact, ...]
    evidence_ladder: Mapping[str, str] = field(default_factory=dict)


_STAGE_SPECS = (
    (
        "request",
        "请求与运行边界",
        "agent_forge.Harness.run",
        "CLI / embedded caller",
        "每次运行先固定 task、配置、workspace 和 artifact 目录。",
        ("execution_environment", "resume_state_loaded"),
    ),
    (
        "loop",
        "有界 AgentLoop",
        "AgentLoop.run",
        "Harness.run",
        "模型只能在 step、timeout、cost 和 failure budget 内推进。",
        ("turn_started", "model_started", "llm_call"),
    ),
    (
        "context_model",
        "上下文与模型调用",
        "TurnPreparation.execute / ModelGateway.chat",
        "AgentLoop._run_turn",
        "模型只看到预算内上下文和本轮允许的工具 schema。",
        ("context_assembly", "context_window", "llm_call"),
    ),
    (
        "tool_governance",
        "工具与副作用治理",
        "ToolExecutionPipeline.execute_calls",
        "AgentLoop._run_turn",
        "所有模型 ToolCall 必须经过路由、幂等、授权和执行证据链。",
        (
            "tool_call",
            "permission_check",
            "operation_ledger",
            "tool_execution_started",
            "tool_observation",
        ),
    ),
    (
        "lifecycle",
        "状态、HITL 与恢复",
        "RunLifecycle.update / stop / request_human_input",
        "AgentLoop / ToolExecutionPipeline",
        "等待或终止必须先持久化 checkpoint，再返回调用方。",
        (
            "task_state_checkpoint",
            "human_input_requested",
            "human_approval",
            "run_completed",
        ),
    ),
    (
        "artifacts",
        "结果与 Artifact",
        "Harness.run",
        "AgentLoop.run",
        "candidate patch、最终文本与运行事实必须分开发布。",
        ("final_answer", "artifact_created"),
    ),
    (
        "evidence",
        "证据投影与 Claim",
        "RunStory projection",
        "Trace / RunManifest",
        "candidate、local 与 official 结论必须使用不同证据分母。",
        ("validation_evidence", "verifier_result"),
    ),
)


def project_run_story(
    manifest: RunManifest,
    trace: Mapping[str, Any] | None = None,
) -> RunStory:
    """把 manifest 和 trace 事实投影为一张黄金主链，不重新判断 solved。"""

    raw_events = trace.get("events") if isinstance(trace, Mapping) else None
    events = [event for event in raw_events or [] if isinstance(event, Mapping)]
    event_types = [str(event.get("event_type") or "") for event in events]
    stages: list[RunStoryStage] = []
    for stage_id, title, owner, upstream, invariant, owned_events in _STAGE_SPECS:
        matching_count = sum(event_type in owned_events for event_type in event_types)
        artifact_ids = tuple(
            artifact.artifact_id
            for artifact in manifest.artifacts
            if artifact.flow_stage == stage_id
        )
        stages.append(
            RunStoryStage(
                stage_id=stage_id,
                title=title,
                owner_symbol=owner,
                canonical_upstream=upstream,
                invariant=invariant,
                observed=bool(matching_count or artifact_ids),
                event_count=matching_count,
                artifact_ids=artifact_ids,
            )
        )

    levels = {"candidate": "unknown", "local": "unknown", "official": "unknown"}
    for artifact in manifest.artifacts:
        if artifact.kind == "patch" and artifact.byte_size > 0:
            levels["candidate"] = "present"
        if artifact.evidence_level in {"local", "official"}:
            levels[artifact.evidence_level] = "present"
    return RunStory(
        run_id=manifest.run_id,
        task=manifest.task,
        status=manifest.status,
        stop_reason=manifest.stop_reason,
        stages=tuple(stages),
        artifacts=manifest.artifacts,
        evidence_ladder=levels,
    )


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value)


__all__ = [
    "RunArtifact",
    "RunManifest",
    "RunStory",
    "RunStoryStage",
    "project_run_story",
]

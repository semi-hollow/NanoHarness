"""里程碑级 Multi-Agent 依赖契约。"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from .fanout import SubagentTask, build_execution_batches


IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
SEMANTIC_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:/-]+$")


class DependencyType(str, Enum):
    """MVP 支持的两种依赖语义。"""

    HARD = "HARD"
    LIVE = "LIVE"


class LiveEventType(str, Enum):
    """Worker 可以向协作 Runtime 提出的结构化事实类型。"""

    READY = "READY"
    FEEDBACK = "FEEDBACK"
    UPDATE = "UPDATE"


class HandoffSeverity(str, Enum):
    """不增加事件类型，只表达 Feedback 的紧急程度。"""

    INFO = "info"
    BLOCKING = "blocking"


@dataclass(frozen=True)
class LiveDependency:
    """经过校验的 producer → consumer 依赖边。

    ``HARD`` 保留完成级调度；``LIVE`` 指定可在 producer 完成前让 consumer
    进入 runnable 状态的里程碑。
    """

    producer_task_id: str
    target_task_id: str
    dependency_type: DependencyType
    semantic_key: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.dependency_type, DependencyType):
            raise ValueError("dependency_type must be HARD or LIVE")
        for label, value in (
            ("producer_task_id", self.producer_task_id),
            ("target_task_id", self.target_task_id),
        ):
            if not IDENTIFIER_PATTERN.fullmatch(value):
                raise ValueError(f"invalid {label}: {value!r}")
        if self.producer_task_id == self.target_task_id:
            raise ValueError("a task cannot depend on itself")
        if self.dependency_type == DependencyType.LIVE:
            if not SEMANTIC_KEY_PATTERN.fullmatch(self.semantic_key):
                raise ValueError("LIVE dependency requires a safe semantic_key")
        elif self.semantic_key:
            raise ValueError("HARD dependency must not declare a semantic_key")

    def to_dict(self) -> dict[str, str]:
        return {
            "producer_task_id": self.producer_task_id,
            "target_task_id": self.target_task_id,
            "dependency_type": self.dependency_type.value,
            "semantic_key": self.semantic_key,
        }


@dataclass(frozen=True)
class LiveHandoffEvent:
    """Worker 提出的协作事实；发布事件不等于获得调度授权。

    ``producer_task_id`` 标识事件发布者。对 ``READY``/``UPDATE``，它是里程碑
    producer；对 ``FEEDBACK``，它是向上游返回证据的 downstream consumer。
    """

    event_type: LiveEventType
    producer_task_id: str
    target_task_id: str
    semantic_key: str
    version: int
    summary: str
    evidence: tuple[str, ...]
    severity: HandoffSeverity = HandoffSeverity.INFO
    emitted_at: float = field(default_factory=time.time, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, LiveEventType):
            raise ValueError("event_type must be READY, FEEDBACK, or UPDATE")
        if not isinstance(self.severity, HandoffSeverity):
            raise ValueError("severity must be info or blocking")
        for label, value in (
            ("producer_task_id", self.producer_task_id),
            ("target_task_id", self.target_task_id),
        ):
            if not IDENTIFIER_PATTERN.fullmatch(value):
                raise ValueError(f"invalid {label}: {value!r}")
        if self.producer_task_id == self.target_task_id:
            raise ValueError("handoff event target must be another task")
        if not SEMANTIC_KEY_PATTERN.fullmatch(self.semantic_key):
            raise ValueError("handoff event requires a safe semantic_key")
        if (
            not isinstance(self.version, int)
            or isinstance(self.version, bool)
            or self.version < 1
        ):
            raise ValueError("handoff event version must be a positive integer")
        if not self.summary.strip() or len(self.summary) > 1_000:
            raise ValueError("handoff event summary must contain 1..1000 characters")
        if not self.evidence or len(self.evidence) > 8:
            raise ValueError("handoff event requires 1..8 evidence items")
        if any(not item.strip() or len(item) > 1_000 for item in self.evidence):
            raise ValueError(
                "handoff event evidence items must contain 1..1000 characters"
            )
        if (
            self.event_type != LiveEventType.FEEDBACK
            and self.severity == HandoffSeverity.BLOCKING
        ):
            raise ValueError("blocking severity is only valid for FEEDBACK")

    @property
    def event_id(self) -> str:
        """返回用于重复检测的稳定内容标识。"""

        payload = {
            "event_type": self.event_type.value,
            "producer_task_id": self.producer_task_id,
            "target_task_id": self.target_task_id,
            "semantic_key": self.semantic_key,
            "version": self.version,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "severity": self.severity.value,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "producer_task_id": self.producer_task_id,
            "target_task_id": self.target_task_id,
            "semantic_key": self.semantic_key,
            "version": self.version,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "severity": self.severity.value,
            "emitted_at": self.emitted_at,
        }


@dataclass(frozen=True)
class LiveHandoffPlan:
    """包含 HARD 与 LIVE 依赖边的小型确定性任务图。"""

    goal: str
    tasks: tuple[SubagentTask, ...]
    dependencies: tuple[LiveDependency, ...] = ()

    def __post_init__(self) -> None:
        if not self.goal.strip():
            raise ValueError("live handoff plan goal must not be empty")
        if not 1 <= len(self.tasks) <= 16:
            raise ValueError("live handoff plan supports 1..16 tasks")
        task_ids = [task.id for task in self.tasks]
        invalid_task_ids = sorted(
            task_id for task_id in task_ids if not IDENTIFIER_PATTERN.fullmatch(task_id)
        )
        if invalid_task_ids:
            raise ValueError(
                f"invalid live handoff task ids: {', '.join(invalid_task_ids)}"
            )
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("live handoff task ids must be unique")
        known_task_ids = set(task_ids)
        unknown = sorted(
            {
                task_id
                for dependency in self.all_dependencies
                for task_id in (
                    dependency.producer_task_id,
                    dependency.target_task_id,
                )
                if task_id not in known_task_ids
            }
        )
        if unknown:
            raise ValueError(f"unknown live handoff task ids: {', '.join(unknown)}")
        identities = [
            (dependency.producer_task_id, dependency.target_task_id)
            for dependency in self.all_dependencies
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("live handoff dependencies must be unique")

        upstream_by_task: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
        for dependency in self.all_dependencies:
            upstream_by_task[dependency.target_task_id].append(
                dependency.producer_task_id
            )
        build_execution_batches(
            [replace(task, depends_on=upstream_by_task[task.id]) for task in self.tasks]
        )

    @property
    def all_dependencies(self) -> tuple[LiveDependency, ...]:
        """合并显式边，并把旧 ``SubagentTask.depends_on`` 解释成 HARD。"""

        explicit_pairs = {
            (dependency.producer_task_id, dependency.target_task_id)
            for dependency in self.dependencies
        }
        legacy_edges: list[LiveDependency] = []
        for task in self.tasks:
            for producer_task_id in task.depends_on:
                if (producer_task_id, task.id) in explicit_pairs:
                    raise ValueError(
                        "dependency edge must be declared either on SubagentTask "
                        "or LiveHandoffPlan, not both"
                    )
                legacy_edges.append(
                    LiveDependency(
                        producer_task_id=producer_task_id,
                        target_task_id=task.id,
                        dependency_type=DependencyType.HARD,
                    )
                )
        return (*self.dependencies, *legacy_edges)

    @property
    def digest(self) -> str:
        encoded = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def dependencies_for(self, task_id: str) -> tuple[LiveDependency, ...]:
        return tuple(
            dependency
            for dependency in self.all_dependencies
            if dependency.target_task_id == task_id
        )

    def live_dependency(
        self,
        *,
        producer_task_id: str,
        target_task_id: str,
        semantic_key: str,
    ) -> LiveDependency | None:
        for dependency in self.all_dependencies:
            if (
                dependency.dependency_type == DependencyType.LIVE
                and dependency.producer_task_id == producer_task_id
                and dependency.target_task_id == target_task_id
                and dependency.semantic_key == semantic_key
            ):
                return dependency
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "tasks": [
                {
                    "id": task.id,
                    "task": task.task,
                    "write_scope": list(task.write_scope),
                    "allowed_tools": list(task.allowed_tools),
                    "expected_artifact": task.expected_artifact,
                    "max_steps": task.max_steps,
                }
                for task in self.tasks
            ],
            "dependencies": [
                dependency.to_dict() for dependency in self.all_dependencies
            ],
        }


@dataclass(frozen=True)
class LiveWorkerCandidate:
    """一个 Worker 的隔离候选结果与机制级校验事实。"""

    payload: dict[str, Any]
    test_passed: bool
    retry_count: int = 0
    rework_count: int = 0
    trajectory_changed: bool = False

    def __post_init__(self) -> None:
        if self.retry_count < 0 or self.rework_count < 0:
            raise ValueError("retry/rework counts must not be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "payload": self.payload,
            "test_passed": self.test_passed,
            "retry_count": self.retry_count,
            "rework_count": self.rework_count,
            "trajectory_changed": self.trajectory_changed,
        }


@dataclass(frozen=True)
class LiveWorkerResult:
    """记录 Worker 时序、候选结果、已消费版本和终态。"""

    task_id: str
    status: str
    started_at_ms: int
    ended_at_ms: int
    candidate: LiveWorkerCandidate | None = None
    consumed_versions: dict[str, int] = field(default_factory=dict)
    error: str = ""

    @property
    def duration_ms(self) -> int:
        return max(0, self.ended_at_ms - self.started_at_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "started_at_ms": self.started_at_ms,
            "ended_at_ms": self.ended_at_ms,
            "duration_ms": self.duration_ms,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "consumed_versions": dict(self.consumed_versions),
            "error": self.error,
        }


@dataclass
class LiveHandoffSummary:
    """单次受控 Live Handoff Run 的 canonical 结果投影。"""

    run_id: str
    scenario: str
    mode: str
    status: str
    plan_digest: str
    wall_time_ms: int
    results: list[LiveWorkerResult]
    handoff_events: list[LiveHandoffEvent]
    timeline: list[dict[str, Any]]
    stale_dependencies: list[dict[str, Any]]
    integration_passed: bool
    integration_detail: str
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "scenario": self.scenario,
            "mode": self.mode,
            "status": self.status,
            "plan_digest": self.plan_digest,
            "wall_time_ms": self.wall_time_ms,
            "results": [result.to_dict() for result in self.results],
            "handoff_events": [event.to_dict() for event in self.handoff_events],
            "timeline": self.timeline,
            "stale_dependencies": self.stale_dependencies,
            "integration_passed": self.integration_passed,
            "integration_detail": self.integration_detail,
            "metrics": self.metrics,
        }

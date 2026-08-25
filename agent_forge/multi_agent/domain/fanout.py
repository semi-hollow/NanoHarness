"""COMMON Fanout 的 Plan、Task、Attempt Result 与持久化契约。

``FanoutPlan`` 在解析后深度不可变；Worker 执行证据与逻辑 Task 治理结果严格分离，
产生 Candidate 不等于 Task 已可信集成。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any, Iterable

from .live_handoff import LiveDependency

# region 1. 深度不可变计划：Domain 构造后集合不能再改变 digest
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
FANOUT_CHECKPOINT_SCHEMA_VERSION = 4
FANOUT_SUMMARY_SCHEMA_VERSION = 4
FANOUT_MECHANISM_EVIDENCE_SCHEMA_VERSION = 4
WORKER_ATTEMPT_STATUSES = {
    "candidate_produced",
    "retryable_failure",
    "terminal_failure",
}
FANOUT_TASK_STATUSES = {"integrated", "failed", "blocked", "not_integrated"}


def _canonical_strings(values: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(str(value).strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError("collection entries must not be empty")
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True)
class SubagentTask:
    """FanoutPlan 中一个深度不可变的可调度任务。"""

    id: str
    task: str
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    write_scope: tuple[str, ...] = field(default_factory=tuple)
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    acceptance_criteria: tuple[str, ...] = field(default_factory=tuple)
    expected_artifact: str = "task_output"
    max_steps: int = 12

    def __post_init__(self) -> None:
        object.__setattr__(self, "depends_on", _canonical_strings(self.depends_on))
        object.__setattr__(self, "write_scope", _canonical_strings(self.write_scope))
        object.__setattr__(self, "allowed_tools", _canonical_strings(self.allowed_tools))
        object.__setattr__(
            self,
            "acceptance_criteria",
            _canonical_strings(self.acceptance_criteria),
        )


@dataclass(frozen=True)
class FanoutConflict:
    """一个 Candidate 无法通过确定性集成门禁的冲突事实。"""

    task_ids: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_ids", _canonical_strings(self.task_ids))


def validate_acyclic_dependencies(
    tasks: Iterable[SubagentTask],
    *,
    extra_dependencies: Mapping[str, Iterable[str]] | None = None,
) -> None:
    """验证唯一 Task identity、已知 dependency 和无环图，不返回调度层。"""

    frozen_tasks = tuple(tasks)
    by_id = {task.id: task for task in frozen_tasks}
    if len(by_id) != len(frozen_tasks):
        raise ValueError("subagent task ids must be unique")
    upstream = {task.id: set(task.depends_on) for task in frozen_tasks}
    for task_id, dependencies in (extra_dependencies or {}).items():
        if task_id not in upstream:
            raise ValueError(f"unknown dependency target: {task_id}")
        upstream[task_id].update(dependencies)
    known = set(by_id)
    unknown = sorted(
        dependency
        for dependencies in upstream.values()
        for dependency in dependencies
        if dependency not in known
    )
    if unknown:
        raise ValueError(f"unknown dependencies: {', '.join(dict.fromkeys(unknown))}")
    remaining = {task_id: set(dependencies) for task_id, dependencies in upstream.items()}
    while remaining:
        ready = [
            task.id
            for task in frozen_tasks
            if task.id in remaining and not (remaining[task.id] & set(remaining))
        ]
        if not ready:
            cycle = ", ".join(task.id for task in frozen_tasks if task.id in remaining)
            raise ValueError(f"cyclic dependencies among subagent tasks: {cycle}")
        for task_id in ready:
            remaining.pop(task_id)


def detect_write_scope_conflicts(
    tasks: Iterable[SubagentTask],
) -> list[FanoutConflict]:
    """返回声明写范围的同路径或父子路径冲突。"""

    frozen_tasks = tuple(tasks)
    conflicts: list[FanoutConflict] = []
    for left_index, left in enumerate(frozen_tasks):
        for right in frozen_tasks[left_index + 1 :]:
            overlap = _first_overlap(left.write_scope, right.write_scope)
            if overlap:
                conflicts.append(
                    FanoutConflict(
                        (left.id, right.id),
                        f"write scopes overlap: {overlap}",
                    )
                )
    return conflicts


def _first_overlap(
    left_paths: Iterable[str],
    right_paths: Iterable[str],
) -> str:
    for left in left_paths:
        for right in right_paths:
            if _paths_overlap(left, right):
                return f"{left} <-> {right}"
    return ""


def _paths_overlap(left: str, right: str) -> bool:
    left_norm = _normalize_path_for_overlap(left)
    right_norm = _normalize_path_for_overlap(right)
    if not left_norm or not right_norm:
        return False
    return (
        left_norm == right_norm
        or left_norm.startswith(f"{right_norm}/")
        or right_norm.startswith(f"{left_norm}/")
    )


def _normalize_path_for_overlap(path: str) -> str:
    return str(path or "").strip().strip("/").rstrip("/")


@dataclass(frozen=True)
class FanoutPlan:
    """整个 Fanout Run 唯一、已校验且深度不可变的计划。"""

    goal: str
    tasks: tuple[SubagentTask, ...]
    global_acceptance_criteria: tuple[str, ...] = field(default_factory=tuple)
    live_dependencies: tuple[LiveDependency, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tasks", tuple(self.tasks))
        object.__setattr__(
            self,
            "global_acceptance_criteria",
            _canonical_strings(self.global_acceptance_criteria),
        )
        object.__setattr__(self, "live_dependencies", tuple(self.live_dependencies))
        if not self.goal.strip():
            raise ValueError("fanout plan goal must not be empty")
        if not 1 <= len(self.tasks) <= 16:
            raise ValueError("fanout plan supports 1..16 tasks")
        if any(not isinstance(task, SubagentTask) for task in self.tasks):
            raise TypeError("fanout plan tasks must be SubagentTask values")
        if any(
            not isinstance(dependency, LiveDependency)
            for dependency in self.live_dependencies
        ):
            raise TypeError("fanout plan live_dependencies must be LiveDependency values")
        _validate_dependency_graph(self.tasks, self.live_dependencies)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "FanoutPlan":
        goal = str(data.get("goal") or "").strip()
        if not goal:
            raise ValueError("fanout plan goal must not be empty")
        rows = data.get("tasks")
        if not isinstance(rows, list) or not rows:
            raise ValueError("fanout plan tasks must be a non-empty list")
        if len(rows) > 16:
            raise ValueError("fanout plan supports at most 16 tasks")
        tasks: list[SubagentTask] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("fanout task must be an object")
            task_id = str(row.get("id") or "").strip()
            task_text = str(row.get("task") or "").strip()
            if task_id in {".", ".."} or not TASK_ID_PATTERN.fullmatch(task_id):
                raise ValueError(f"invalid fanout task id: {task_id!r}")
            if not task_text:
                raise ValueError(f"fanout task {task_id!r} has no task text")
            max_steps = row.get("max_steps", 12)
            if (
                isinstance(max_steps, bool)
                or not isinstance(max_steps, int)
                or not 2 <= max_steps <= 32
            ):
                raise ValueError(
                    f"fanout task {task_id!r} max_steps must be an integer from 2 to 32"
                )
            expected_artifact = str(
                row.get("expected_artifact") or "task_output"
            ).strip()
            if expected_artifact in {"", ".", ".."} or not TASK_ID_PATTERN.fullmatch(
                expected_artifact
            ):
                raise ValueError(
                    f"fanout task {task_id!r} expected_artifact must be a safe file name"
                )
            tasks.append(
                SubagentTask(
                    id=task_id,
                    task=task_text,
                    depends_on=tuple(_dependency_list(row)),
                    write_scope=tuple(
                        _normalize_scope(value)
                        for value in _string_list(row, "write_scope")
                    ),
                    allowed_tools=tuple(_string_list(row, "allowed_tools")),
                    acceptance_criteria=tuple(
                        _criteria_list(row, "acceptance_criteria")
                    ),
                    expected_artifact=expected_artifact,
                    max_steps=max_steps,
                )
            )
        live_rows = data.get("live_dependencies", [])
        if not isinstance(live_rows, list):
            raise ValueError("fanout plan live_dependencies must be a list")
        return cls(
            goal=goal,
            tasks=tuple(tasks),
            global_acceptance_criteria=tuple(
                _criteria_list(data, "global_acceptance_criteria")
            ),
            live_dependencies=tuple(
                LiveDependency.from_mapping(row) for row in live_rows
            ),
        )

    @property
    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def live_task_ids(self) -> frozenset[str]:
        return frozenset(
            task_id
            for edge in self.live_dependencies
            for task_id in (edge.producer_task_id, edge.target_task_id)
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "goal": self.goal,
            "tasks": [_task_to_dict(task) for task in self.tasks],
        }
        if self.global_acceptance_criteria:
            payload["global_acceptance_criteria"] = list(
                self.global_acceptance_criteria
            )
        if self.live_dependencies:
            payload["live_dependencies"] = [
                dependency.to_dict() for dependency in self.live_dependencies
            ]
        return payload

    def live_dependencies_for(self, task_id: str) -> tuple[LiveDependency, ...]:
        return tuple(
            dependency
            for dependency in self.live_dependencies
            if dependency.target_task_id == task_id
        )

    def live_routes_for(self, task_id: str) -> tuple[LiveDependency, ...]:
        return tuple(
            dependency
            for dependency in self.live_dependencies
            if task_id in {dependency.producer_task_id, dependency.target_task_id}
        )

    def integration_order(self, task_ids: Iterable[str]) -> list[str]:
        selected = set(task_ids)
        dependencies = {
            task.id: set(task.depends_on)
            | {
                edge.producer_task_id
                for edge in self.live_dependencies
                if edge.target_task_id == task.id
            }
            for task in self.tasks
            if task.id in selected
        }
        ordered: list[str] = []
        while dependencies:
            ready = [
                task.id
                for task in self.tasks
                if task.id in dependencies
                and not (dependencies[task.id] & set(dependencies))
            ]
            if not ready:  # pragma: no cover - constructor validates the graph
                raise AssertionError("validated fanout plan contains a cycle")
            ordered.extend(ready)
            for task_id in ready:
                dependencies.pop(task_id)
        return ordered
# endregion 1. 深度不可变计划结束


# region 2. 结果与持久化契约：真实 Attempt 与逻辑 Task 治理分离
@dataclass(frozen=True)
class WorkerHandoff:
    """只传给直接 HARD 后继的有界语义结果。"""

    task_id: str
    summary: str
    touched_files: tuple[str, ...] = field(default_factory=tuple)
    validation_evidence: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    unresolved_issues: tuple[str, ...] = field(default_factory=tuple)
    artifact_path: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "touched_files", tuple(self.touched_files))
        object.__setattr__(
            self,
            "validation_evidence",
            tuple(dict(item) for item in self.validation_evidence),
        )
        object.__setattr__(
            self,
            "unresolved_issues",
            tuple(self.unresolved_issues),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CriterionResult:
    criterion: str
    status: str
    evidence: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class WorkerAttemptResult:
    """恰好一次真实 Worker execution 产生的证据。"""

    task_id: str
    attempt: int
    launch_wave_index: int
    status: str
    failure_kind: str = ""
    retryable: bool = False
    final_answer: str = ""
    summary: str = ""
    touched_files: list[str] = field(default_factory=list)
    candidate_diff_path: str = ""
    candidate_diff_sha256: str = ""
    artifact_path: str = ""
    trace_path: str = ""
    usage_path: str = ""
    environment_manifest_path: str = ""
    duration_ms: int = 0
    validation_evidence: list[dict[str, Any]] = field(default_factory=list)
    unresolved_issues: list[str] = field(default_factory=list)
    handoff: WorkerHandoff | None = None
    error: str = ""
    workspace: str = ""
    usage_summary: dict[str, Any] = field(default_factory=dict)
    stop_reason: str = ""
    resumed: bool = False

    def __post_init__(self) -> None:
        if self.status not in WORKER_ATTEMPT_STATUSES:
            raise ValueError(f"invalid WorkerAttemptResult status: {self.status}")
        if self.attempt not in {1, 2}:
            raise ValueError("WorkerAttemptResult attempt must be 1 or 2")
        if self.launch_wave_index < 1:
            raise ValueError("launch_wave_index must start at 1")
        if self.retryable != (self.status == "retryable_failure"):
            raise ValueError(
                "retryable must be true exactly when status=retryable_failure"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FanoutTaskResult:
    """逻辑 Task 的治理结果，不等同于任意一次 Attempt。"""

    task_id: str
    status: str
    failure_kind: str = ""
    final_attempt: int | None = None
    handoff: WorkerHandoff | None = None
    error: str = ""
    unresolved_issues: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.status not in FANOUT_TASK_STATUSES:
            raise ValueError(f"invalid FanoutTaskResult status: {self.status}")
        if self.final_attempt not in {None, 1, 2}:
            raise ValueError("FanoutTaskResult final_attempt must be None, 1, or 2")
        object.__setattr__(self, "unresolved_issues", tuple(self.unresolved_issues))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def project_worker_handoff(result: WorkerAttemptResult) -> WorkerHandoff:
    summary = " ".join((result.summary or result.final_answer or "").split())[:1200]
    issues = list(result.unresolved_issues)
    if result.status != "candidate_produced" and not issues:
        issues.append(result.error or f"worker ended with status {result.status}")
    return WorkerHandoff(
        task_id=result.task_id,
        summary=summary,
        touched_files=tuple(result.touched_files),
        validation_evidence=tuple(dict(item) for item in result.validation_evidence),
        unresolved_issues=tuple(dict.fromkeys(issue for issue in issues if issue)),
        artifact_path=result.artifact_path,
    )


@dataclass(frozen=True)
class FanoutCheckpoint:
    plan_digest: str
    base_head: str
    status: str
    merged_task_ids: tuple[str, ...]
    task_results: tuple[FanoutTaskResult, ...]
    attempt_results: tuple[WorkerAttemptResult, ...]
    launch_waves: tuple[tuple[dict[str, int | str], ...], ...]
    updated_at: float = field(default_factory=time.time)
    schema_version: int = FANOUT_CHECKPOINT_SCHEMA_VERSION


@dataclass
class FanoutSummary:
    schema_version: int
    run_id: str
    goal: str
    status: str
    plan_digest: str
    base_head: str
    launch_waves: list[list[dict[str, int | str]]]
    task_results: list[FanoutTaskResult]
    attempt_results: list[WorkerAttemptResult]
    merged_task_ids: list[str]
    conflicts: list[FanoutConflict]
    wall_time_ms: int
    metrics: dict[str, Any]
    final_decision: str = ""
    final_answer: str = ""
    finalizer_trace_path: str = ""
    finalizer_usage_path: str = ""
    finalizer_usage_summary: dict[str, Any] = field(default_factory=dict)
    criterion_results: list[CriterionResult] = field(default_factory=list)
    summary_path: str = ""
    report_path: str = ""
    integrated_diff_path: str = ""
    integration_frontier_task_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "goal": self.goal,
            "status": self.status,
            "plan_digest": self.plan_digest,
            "base_head": self.base_head,
            "launch_waves": self.launch_waves,
            "task_results": [result.to_dict() for result in self.task_results],
            "attempt_results": [result.to_dict() for result in self.attempt_results],
            "merged_task_ids": list(self.merged_task_ids),
            "conflicts": [asdict(conflict) for conflict in self.conflicts],
            "wall_time_ms": self.wall_time_ms,
            "metrics": self.metrics,
            "final_decision": self.final_decision,
            "final_answer": self.final_answer,
            "finalizer_trace_path": self.finalizer_trace_path,
            "finalizer_usage_path": self.finalizer_usage_path,
            "finalizer_usage_summary": self.finalizer_usage_summary,
            "criterion_results": [result.to_dict() for result in self.criterion_results],
            "summary_path": self.summary_path,
            "report_path": self.report_path,
            "integrated_diff_path": self.integrated_diff_path,
            "integration_frontier_task_id": self.integration_frontier_task_id,
        }


@dataclass(frozen=True)
class FinalizerResult:
    decision: str
    answer: str
    trace_path: str
    usage_path: str
    usage_summary: dict[str, Any]
    criterion_results: tuple[CriterionResult, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "criterion_results", tuple(self.criterion_results))


def aggregate_fanout_metrics(
    task_count: int,
    attempt_results: list[WorkerAttemptResult],
    wall_time_ms: int,
    *,
    max_workers: int,
    finalizer_usage: dict[str, Any],
) -> dict[str, Any]:
    """分别按 Plan Task、真实 Attempt 与 Finalizer 聚合 canonical 指标。"""

    # region 1. 固定计数口径与 Worker 时长
    # Task 分母来自 Plan，Attempt 分母只来自真实 WorkerAttemptResult。
    keys = (
        "llm_calls",
        "total_tokens",
        "estimated_cost_usd",
        "llm_latency_ms",
        "tool_calls",
        "failed_tool_calls",
    )
    current_duration = sum(
        result.duration_ms for result in attempt_results if not result.resumed
    )
    resumed_duration = sum(
        result.duration_ms for result in attempt_results if result.resumed
    )
    metrics: dict[str, Any] = {
        "task_count": task_count,
        "attempt_count": len(attempt_results),
        "candidate_count": sum(
            result.status == "candidate_produced" for result in attempt_results
        ),
        "resumed_attempt_count": sum(result.resumed for result in attempt_results),
        "max_workers": max_workers,
        "wall_time_ms": wall_time_ms,
        "summed_worker_duration_ms": current_duration + resumed_duration,
        "current_worker_duration_ms": current_duration,
        "resumed_worker_duration_ms": resumed_duration,
        "worker_time_to_wall_ratio": (
            round(current_duration / wall_time_ms, 4) if wall_time_ms else 0.0
        ),
    }
    # endregion 1. 固定计数口径与 Worker 时长

    # region 2. 当前 Run 与恢复证据链用量分账
    # 恢复 Attempt 只计入 evidence chain，不重复计入本次实际消费。
    for key in keys:
        current_worker = sum(
            float(result.usage_summary.get(key) or 0)
            for result in attempt_results
            if not result.resumed
        )
        resumed_worker = sum(
            float(result.usage_summary.get(key) or 0)
            for result in attempt_results
            if result.resumed
        )
        current = current_worker + float(finalizer_usage.get(key) or 0)
        evidence_chain = current + resumed_worker
        if key == "estimated_cost_usd":
            metrics[key] = round(current, 6)
            metrics[f"resumed_{key}"] = round(resumed_worker, 6)
            metrics[f"evidence_chain_{key}"] = round(evidence_chain, 6)
        else:
            metrics[key] = int(current)
            metrics[f"resumed_{key}"] = int(resumed_worker)
            metrics[f"evidence_chain_{key}"] = int(evidence_chain)
    # endregion 2. 当前 Run 与恢复证据链用量分账

    # region 3. Finalizer 独立投影与发布
    metrics["finalizer_llm_calls"] = int(finalizer_usage.get("llm_calls") or 0)
    # Finalizer 不产生 Worker Attempt，因此只作为独立用量字段进入 Summary。
    return metrics
    # endregion 3. Finalizer 独立投影与发布
# endregion 2. 结果与持久化契约结束


# region 3. 输入规范化与依赖图校验：JSON list 只停留在解析边界
def _normalize_scope(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"write scope must be a relative workspace path: {value!r}")
    normalized = path.as_posix()
    if not normalized or normalized == ".":
        raise ValueError(f"write scope must be a relative workspace path: {value!r}")
    return normalized.rstrip("/") + ("/" if text.endswith("/") else "")


def _string_list(data: dict[str, Any], key: str) -> list[str]:
    values = data.get(key)
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError(f"fanout task {key} must be a list")
    normalized = [str(value).strip() for value in values]
    if any(not value for value in normalized):
        raise ValueError(f"fanout task {key} entries must not be empty")
    return list(dict.fromkeys(normalized))


def _criteria_list(data: dict[str, Any], key: str) -> list[str]:
    criteria = _string_list(data, key)
    if len(criteria) > 16:
        raise ValueError(f"{key} supports at most 16 entries")
    if any(len(criterion) > 500 for criterion in criteria):
        raise ValueError(f"{key} entries support at most 500 characters")
    return criteria


def _dependency_list(data: dict[str, Any]) -> list[str]:
    values = _string_list(data, "depends_on")
    raw = data.get("depends_on")
    if isinstance(raw, list) and len(raw) != len(values):
        raise ValueError("fanout task HARD dependencies must be unique")
    return values


def _task_to_dict(task: SubagentTask) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": task.id,
        "task": task.task,
        "depends_on": list(task.depends_on),
        "write_scope": list(task.write_scope),
        "allowed_tools": list(task.allowed_tools),
        "expected_artifact": task.expected_artifact,
        "max_steps": task.max_steps,
    }
    if task.acceptance_criteria:
        payload["acceptance_criteria"] = list(task.acceptance_criteria)
    return payload


def _validate_dependency_graph(
    tasks: tuple[SubagentTask, ...],
    live_dependencies: tuple[LiveDependency, ...],
) -> None:
    task_ids = [task.id for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("subagent task ids must be unique")
    known = set(task_ids)
    for task in tasks:
        if task.id in task.depends_on:
            raise ValueError(f"task {task.id} cannot depend on itself")
    hard_pairs = {
        (dependency, task.id) for task in tasks for dependency in task.depends_on
    }
    live_identities = [
        (edge.producer_task_id, edge.target_task_id, edge.semantic_key)
        for edge in live_dependencies
    ]
    if len(live_identities) != len(set(live_identities)):
        raise ValueError("LIVE semantic dependencies must be unique")
    live_pairs = {(producer, target) for producer, target, _ in live_identities}
    if len(live_pairs) != len(live_identities):
        raise ValueError("a producer-target pair may declare only one LIVE dependency")
    unknown = sorted(
        {
            endpoint
            for edge in live_dependencies
            for endpoint in (edge.producer_task_id, edge.target_task_id)
            if endpoint not in known
        }
        | {dependency for dependency, _ in hard_pairs if dependency not in known}
    )
    if unknown:
        raise ValueError(f"unknown dependencies: {', '.join(unknown)}")
    overlap = sorted(hard_pairs & live_pairs)
    if overlap:
        producer, target = overlap[0]
        raise ValueError(
            f"dependency {producer} -> {target} cannot be both HARD and LIVE"
        )
    validate_acyclic_dependencies(tasks)
    live_upstream: dict[str, list[str]] = {}
    for edge in live_dependencies:
        live_upstream.setdefault(edge.target_task_id, []).append(edge.producer_task_id)
    validate_acyclic_dependencies(tasks, extra_dependencies=live_upstream)
    conflicts = detect_write_scope_conflicts(
        task for task in tasks if task.id in {
            task_id
            for edge in live_dependencies
            for task_id in (edge.producer_task_id, edge.target_task_id)
        }
    )
    if conflicts:
        raise ValueError(
            "LIVE workers require non-overlapping write scopes: "
            + conflicts[0].reason
        )
# endregion 3. 输入规范化与依赖图校验结束

"""真实 AgentLoop fanout 的计划与结果模型。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any

from .fanout import (
    FanoutConflict,
    SubagentTask,
    build_execution_batches,
    detect_write_scope_conflicts,
)
from .live_handoff import LiveDependency

TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


# 核心数据：经过校验并可恢复的 live fanout 任务 DAG。
@dataclass(frozen=True)
class FanoutPlan:
    """经过验证、可确定性调度的任务 DAG。

    ``goal`` 是整体目标，``tasks`` 是带依赖和写范围的子任务。
    ``digest`` 对规范化
    计划做内容哈希，恢复时拒绝计划漂移。
    """

    goal: str
    tasks: list[SubagentTask]
    global_acceptance_criteria: list[str] = field(default_factory=list)
    live_dependencies: list[LiveDependency] = field(default_factory=list)

    def __post_init__(self) -> None:
        """集中维护 HARD/LIVE 图的全部安全不变量。"""

        if not self.goal.strip():
            raise ValueError("fanout plan goal must not be empty")
        if not 1 <= len(self.tasks) <= 16:
            raise ValueError("fanout plan supports 1..16 tasks")
        _validate_dependency_graph(self.tasks, self.live_dependencies)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "FanoutPlan":
        """在 JSON 边界之后验证计划结构和依赖关系。"""

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
                    depends_on=_dependency_list(row),
                    write_scope=[
                        _normalize_scope(value)
                        for value in _string_list(row, "write_scope")
                    ],
                    allowed_tools=_string_list(row, "allowed_tools"),
                    acceptance_criteria=_criteria_list(
                        row,
                        "acceptance_criteria",
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
            tasks=tasks,
            global_acceptance_criteria=_criteria_list(
                data,
                "global_acceptance_criteria",
            ),
            live_dependencies=[
                LiveDependency.from_mapping(row) for row in live_rows
            ],
        )

    @property
    def digest(self) -> str:
        """返回恢复校验使用的稳定计划摘要。"""

        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "goal": self.goal,
            "tasks": [_task_to_dict(task) for task in self.tasks],
        }
        # 空 V1 字段不落盘，确保 V0 计划摘要和检查点继续有效。
        if self.global_acceptance_criteria:
            payload["global_acceptance_criteria"] = list(
                self.global_acceptance_criteria
            )
        if self.live_dependencies:
            payload["live_dependencies"] = [
                dependency.to_dict() for dependency in self.live_dependencies
            ]
        return payload

    def live_dependencies_for(self, task_id: str) -> list[LiveDependency]:
        return [
            dependency
            for dependency in self.live_dependencies
            if dependency.target_task_id == task_id
        ]

    def live_routes_for(self, task_id: str) -> list[LiveDependency]:
        """返回 task 可作为 producer 或 consumer 参与的有效 LIVE 边。"""

        return [
            dependency
            for dependency in self.live_dependencies
            if task_id
            in {dependency.producer_task_id, dependency.target_task_id}
        ]

    def integration_order(self, task_ids: set[str]) -> list[str]:
        """按 HARD + LIVE 组合图返回当前结果的稳定集成顺序。"""

        dependencies = {
            task.id: set(task.depends_on)
            | {
                edge.producer_task_id
                for edge in self.live_dependencies
                if edge.target_task_id == task.id
            }
            for task in self.tasks
            if task.id in task_ids
        }
        ordered: list[str] = []
        while dependencies:
            ready = [
                task.id
                for task in self.tasks
                if task.id in dependencies
                and not (dependencies[task.id] & set(dependencies))
            ]
            if not ready:  # pragma: no cover - FanoutPlan validation protects this
                raise AssertionError("validated fanout plan contains a cycle")
            ordered.extend(ready)
            for task_id in ready:
                dependencies.pop(task_id)
        return ordered


@dataclass(frozen=True)
class WorkerHandoff:
    """上游结果向直接依赖任务传递的紧凑、确定性语义状态。"""

    task_id: str
    status: str
    summary: str
    touched_files: list[str] = field(default_factory=list)
    validation_evidence: list[dict[str, Any]] = field(default_factory=list)
    unresolved_issues: list[str] = field(default_factory=list)
    artifact_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CriterionResult:
    """Finalizer 对一条验收标准的显式判断。"""

    criterion: str
    status: str
    evidence: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


# 核心数据：真实 AgentLoop worker 的结果、候选 diff 与 evidence 位置。
@dataclass
class LiveSubagentResult:
    """一个隔离 worker 的规范化结果和证据位置。

    task/status/final_answer 描述结果；touched_files 与 candidate diff hash 支撑冲突校验；
    workspace、trace、usage、candidate diff、artifact、environment path 指向真实证据；
    batch/duration/usage/resumed 记录调度与恢复事实。
    """

    task_id: str
    status: str
    final_answer: str = ""
    touched_files: list[str] = field(default_factory=list)
    workspace: str = ""
    trace_path: str = ""
    usage_path: str = ""
    candidate_diff_path: str = ""
    candidate_diff_sha256: str = ""
    artifact_path: str = ""
    environment_manifest_path: str = ""
    batch_index: int = 0
    error: str = ""
    duration_ms: int = 0
    usage_summary: dict[str, Any] = field(default_factory=dict)
    resumed: bool = False
    attempt: int = 1
    stop_reason: str = ""
    failure_kind: str = ""
    retryable: bool = False
    validation_evidence: list[dict[str, Any]] = field(default_factory=list)
    unresolved_issues: list[str] = field(default_factory=list)
    handoff: WorkerHandoff | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def project_worker_handoff(result: LiveSubagentResult) -> WorkerHandoff:
    """从结果事实投影稳定 Handoff；不读取或复制 Worker 私有会话。"""

    summary = " ".join((result.final_answer or "").split())[:1200]
    issues = list(result.unresolved_issues)
    if result.status != "completed" and not issues:
        issues.append(result.error or f"worker ended with status {result.status}")
    return WorkerHandoff(
        task_id=result.task_id,
        status=result.status,
        summary=summary,
        touched_files=list(result.touched_files),
        validation_evidence=[dict(item) for item in result.validation_evidence],
        unresolved_issues=list(dict.fromkeys(issue for issue in issues if issue)),
        artifact_path=result.artifact_path,
    )


# 核心数据：fanout 中途恢复点的计划身份、结果和合并进度。
@dataclass(frozen=True)
class FanoutCheckpoint:
    """写入 durable checkpoint 的完整快照。"""

    plan_digest: str
    base_head: str
    results: list[LiveSubagentResult]
    merged_task_ids: list[str]
    status: str
    initial_plan_identity: dict[str, str] = field(default_factory=dict)
    effective_plan: FanoutPlan | None = None
    effective_plan_digest: str = ""
    replan_round: int = 0
    attempt_results: list[LiveSubagentResult] = field(default_factory=list)


# 核心数据：live fanout 调度、合并、冲突、finalizer 和 artifact 的最终汇总。
@dataclass
class LiveFanoutSummary:
    """Live fanout 当前运行和恢复证据的聚合结果。

    identity 字段记录 run/goal/plan/base；batches/results/merged/conflicts 记录调度与
    合并；status、wall_time、metrics 和 finalizer 字段记录最终判断与成本；
    末尾 path 字段指向 summary、report 和 integrated diff，便于 UI/评测读取，
    不重算事实。
    """

    run_id: str
    goal: str
    status: str
    plan_digest: str
    base_head: str
    batches: list[list[str]]
    results: list[LiveSubagentResult]
    merged_task_ids: list[str]
    conflicts: list[FanoutConflict]
    wall_time_ms: int
    metrics: dict[str, Any]
    final_decision: str = ""
    final_answer: str = ""
    finalizer_trace_path: str = ""
    finalizer_usage_path: str = ""
    finalizer_usage_summary: dict[str, Any] = field(default_factory=dict)
    summary_path: str = ""
    report_path: str = ""
    integrated_diff_path: str = ""
    initial_plan_identity: dict[str, str] = field(default_factory=dict)
    effective_plan: dict[str, Any] = field(default_factory=dict)
    effective_plan_digest: str = ""
    replan_round: int = 0
    attempt_results: list[LiveSubagentResult] = field(default_factory=list)
    criterion_results: list[CriterionResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "status": self.status,
            "plan_digest": self.plan_digest,
            "base_head": self.base_head,
            "batches": self.batches,
            "results": [result.to_dict() for result in self.results],
            "merged_task_ids": self.merged_task_ids,
            "conflicts": [asdict(conflict) for conflict in self.conflicts],
            "wall_time_ms": self.wall_time_ms,
            "metrics": self.metrics,
            "final_decision": self.final_decision,
            "final_answer": self.final_answer,
            "finalizer_trace_path": self.finalizer_trace_path,
            "finalizer_usage_path": self.finalizer_usage_path,
            "finalizer_usage_summary": self.finalizer_usage_summary,
            "summary_path": self.summary_path,
            "report_path": self.report_path,
            "integrated_diff_path": self.integrated_diff_path,
            "initial_plan_identity": self.initial_plan_identity,
            "effective_plan": self.effective_plan,
            "effective_plan_digest": self.effective_plan_digest,
            "replan_round": self.replan_round,
            "attempt_results": [result.to_dict() for result in self.attempt_results],
            "criterion_results": [
                result.to_dict() for result in self.criterion_results
            ],
        }


@dataclass(frozen=True)
class FinalizerResult:
    """只读整合验证器返回的决定和证据位置。"""

    decision: str
    answer: str
    trace_path: str
    usage_path: str
    usage_summary: dict[str, Any]
    criterion_results: list[CriterionResult] = field(default_factory=list)


def aggregate_live_metrics(
    results: list[LiveSubagentResult],
    wall_time_ms: int,
    *,
    max_workers: int,
    finalizer_usage: dict[str, Any],
) -> dict[str, Any]:
    """区分本次消耗、恢复历史和完整证据链消耗。"""

    keys = (
        "llm_calls",
        "total_tokens",
        "estimated_cost_usd",
        "llm_latency_ms",
        "tool_calls",
        "failed_tool_calls",
    )
    current_worker_duration_ms = sum(
        result.duration_ms for result in results if not result.resumed
    )
    resumed_worker_duration_ms = sum(
        result.duration_ms for result in results if result.resumed
    )
    metrics: dict[str, Any] = {
        "task_count": len({result.task_id for result in results}),
        "attempt_count": len(results),
        "completed_count": sum(result.status == "completed" for result in results),
        "resumed_count": sum(result.resumed for result in results),
        "max_workers": max_workers,
        "wall_time_ms": wall_time_ms,
        "summed_worker_duration_ms": sum(result.duration_ms for result in results),
        "current_worker_duration_ms": current_worker_duration_ms,
        "resumed_worker_duration_ms": resumed_worker_duration_ms,
        "worker_time_to_wall_ratio": round(
            current_worker_duration_ms / wall_time_ms,
            4,
        )
        if wall_time_ms
        else 0.0,
    }
    for key in keys:
        current_worker_value = sum(
            float(result.usage_summary.get(key) or 0)
            for result in results
            if not result.resumed
        )
        resumed_worker_value = sum(
            float(result.usage_summary.get(key) or 0)
            for result in results
            if result.resumed
        )
        current_value = current_worker_value + float(finalizer_usage.get(key) or 0)
        evidence_chain_value = current_value + resumed_worker_value
        if key == "estimated_cost_usd":
            metrics[key] = round(current_value, 6)
            metrics[f"resumed_{key}"] = round(resumed_worker_value, 6)
            metrics[f"evidence_chain_{key}"] = round(evidence_chain_value, 6)
        else:
            metrics[key] = int(current_value)
            metrics[f"resumed_{key}"] = int(resumed_worker_value)
            metrics[f"evidence_chain_{key}"] = int(evidence_chain_value)
    metrics["finalizer_llm_calls"] = int(finalizer_usage.get("llm_calls") or 0)
    return metrics


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
    dependencies = data.get("depends_on")
    if dependencies is None:
        return []
    if not isinstance(dependencies, list):
        raise ValueError("fanout task depends_on must be a list")
    normalized = [str(value).strip() for value in dependencies]
    if any(not value for value in normalized):
        raise ValueError("fanout task depends_on entries must not be empty")
    if len(normalized) != len(set(normalized)):
        raise ValueError("fanout task HARD dependencies must be unique")
    return normalized


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
    tasks: list[SubagentTask],
    live_dependencies: list[LiveDependency],
) -> None:
    """一次性验证唯一 FanoutPlan 的 HARD、LIVE 和组合并发边界。"""

    task_ids = [task.id for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("subagent task ids must be unique")
    known = set(task_ids)
    for task in tasks:
        if task.id in task.depends_on:
            raise ValueError(f"task {task.id} cannot depend on itself")
        if len(task.depends_on) != len(set(task.depends_on)):
            raise ValueError(f"task {task.id} HARD dependencies must be unique")
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

    # 复用 HARD 图校验，再校验组合图，统一阻断依赖环。
    build_execution_batches(tasks)
    upstream_by_task = {task.id: list(task.depends_on) for task in tasks}
    for edge in live_dependencies:
        upstream_by_task[edge.target_task_id].append(edge.producer_task_id)
    build_execution_batches(
        [
            SubagentTask(
                id=task.id,
                task=task.task,
                depends_on=upstream_by_task[task.id],
                write_scope=task.write_scope,
            )
            for task in tasks
        ]
    )

    live_task_ids = {
        task_id
        for edge in live_dependencies
        for task_id in (edge.producer_task_id, edge.target_task_id)
    }
    conflicts = detect_write_scope_conflicts(
        [task for task in tasks if task.id in live_task_ids]
    )
    if conflicts:
        raise ValueError(
            "LIVE workers require non-overlapping write scopes: "
            + conflicts[0].reason
        )

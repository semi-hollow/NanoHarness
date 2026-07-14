"""真实 AgentLoop fanout 的计划与结果模型。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any

from .fanout import FanoutConflict, SubagentTask, build_execution_batches


TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class FanoutPlan:
    """经过验证、可确定性调度的任务 DAG。"""

    goal: str
    tasks: list[SubagentTask]

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
            if (
                expected_artifact in {"", ".", ".."}
                or not TASK_ID_PATTERN.fullmatch(expected_artifact)
            ):
                raise ValueError(
                    f"fanout task {task_id!r} expected_artifact must be a safe file name"
                )
            tasks.append(
                SubagentTask(
                    id=task_id,
                    task=task_text,
                    depends_on=_string_list(row, "depends_on"),
                    write_scope=[
                        _normalize_scope(value)
                        for value in _string_list(row, "write_scope")
                    ],
                    allowed_tools=_string_list(row, "allowed_tools"),
                    expected_artifact=expected_artifact,
                    max_steps=max_steps,
                )
            )
        build_execution_batches(tasks)
        return cls(goal=goal, tasks=tasks)

    @property
    def digest(self) -> str:
        """返回恢复校验使用的稳定计划摘要。"""

        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "tasks": [
                {
                    "id": task.id,
                    "task": task.task,
                    "depends_on": task.depends_on,
                    "write_scope": task.write_scope,
                    "allowed_tools": task.allowed_tools,
                    "expected_artifact": task.expected_artifact,
                    "max_steps": task.max_steps,
                }
                for task in self.tasks
            ],
        }


@dataclass
class LiveSubagentResult:
    """一个隔离 worker 的规范化结果和证据位置。"""

    task_id: str
    status: str
    final_answer: str = ""
    touched_files: list[str] = field(default_factory=list)
    workspace: str = ""
    trace_path: str = ""
    usage_path: str = ""
    patch_path: str = ""
    patch_sha256: str = ""
    artifact_path: str = ""
    environment_manifest_path: str = ""
    batch_index: int = 0
    error: str = ""
    duration_ms: int = 0
    usage_summary: dict[str, Any] = field(default_factory=dict)
    resumed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LiveFanoutSummary:
    """Live fanout 当前运行和恢复证据的聚合结果。"""

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
    integration_patch_path: str = ""

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
            "integration_patch_path": self.integration_patch_path,
        }


@dataclass(frozen=True)
class FinalizerResult:
    """只读整合验证器返回的决定和证据位置。"""

    decision: str
    answer: str
    trace_path: str
    usage_path: str
    usage_summary: dict[str, Any]


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
        "task_count": len(results),
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

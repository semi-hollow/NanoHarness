"""Multi-Agent 执行计划、Worker 结果和最终汇总的 canonical Domain contract。

系统角色：定义从 Planner 到 Coordinator、再到 artifact/Workbench 的稳定数据语言。
输入：外部 mapping、真实 Worker evidence 和 Finalizer 结果。
输出：可哈希 ``FanoutPlan``、紧凑 ``WorkerHandoff``、checkpoint 与 summary。
相邻边界：本文件不调用模型、不创建线程、不操作 Git；所有执行行为由 Application
拥有，所有文件 IO 由 Adapter 拥有。

折叠导航：1 唯一计划；2 结果与持久化 contract；3 metrics；4 输入规范化与图校验。
"""

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


# region 1. 唯一执行计划：JSON mapping → typed tasks → HARD/LIVE 组合图校验 → digest
# 核心数据：经过校验并可恢复的 live fanout 任务 DAG。
@dataclass(frozen=True)
class FanoutPlan:
    """经过验证、可确定性调度的任务 DAG。

    ``goal`` 是整体目标，``tasks`` 是带依赖和写范围的子任务。
    ``digest`` 对规范化计划做内容哈希，恢复时拒绝计划漂移。
    """

    goal: str
    tasks: list[SubagentTask]
    global_acceptance_criteria: list[str] = field(default_factory=list)
    live_dependencies: list[LiveDependency] = field(default_factory=list)

    def __post_init__(self) -> None:
        """集中维护 HARD/LIVE 图的全部安全不变量。"""

        # 整体目标为空时无法解释 Planner/Finalizer 的共同验收对象。
        if not self.goal.strip():
            raise ValueError("fanout plan goal must not be empty")
        # Fanout 必须至少一个 Task，并限制规模以控制 Prompt、线程和校验成本。
        if not 1 <= len(self.tasks) <= 16:
            raise ValueError("fanout plan supports 1..16 tasks")
        _validate_dependency_graph(self.tasks, self.live_dependencies)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "FanoutPlan":
        """在 JSON 边界之后验证计划结构和依赖关系。

        伪代码：校验 goal/tasks 容器 -> 逐 Task 收窄字段和预算
        -> 解析 LIVE edges -> 构造 ``FanoutPlan`` 并统一校验组合图。
        """

        # region 1. Task contract：逐个收敛 id、文本、scope、tools、criteria 和 step budget
        goal = str(data.get("goal") or "").strip()
        # goal 是所有 Task 和 Finalizer 的共同业务目标，必须明确存在。
        if not goal:
            raise ValueError("fanout plan goal must not be empty")
        rows = data.get("tasks")
        # 物理边界要求非空 JSON array，不能接受单对象或隐式默认 Task。
        if not isinstance(rows, list) or not rows:
            raise ValueError("fanout plan tasks must be a non-empty list")
        # 控制最大 fanout，避免模型通过计划字段放大运行资源。
        if len(rows) > 16:
            raise ValueError("fanout plan supports at most 16 tasks")
        tasks: list[SubagentTask] = []
        # 逐行把不可信 mapping 收窄成 typed SubagentTask。
        for row in rows:
            # 每个 Task 必须是独立 object，不能接受字符串等快捷形式。
            if not isinstance(row, dict):
                raise ValueError("fanout task must be an object")
            task_id = str(row.get("id") or "").strip()
            task_text = str(row.get("task") or "").strip()
            # ID 同时用于目录、事件和依赖引用，只允许安全且稳定的字符。
            if task_id in {".", ".."} or not TASK_ID_PATTERN.fullmatch(task_id):
                raise ValueError(f"invalid fanout task id: {task_id!r}")
            # 没有任务文本就不存在可交给 Worker 的目标。
            if not task_text:
                raise ValueError(f"fanout task {task_id!r} has no task text")
            max_steps = row.get("max_steps", 12)
            # bool 在 Python 中属于 int，必须显式排除；预算限制为 2..32。
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
            # Artifact 名称会进入文件路径，因此使用与 Task ID 相同的安全规则。
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
        # endregion 1. Task contract 结束

        # region 2. LIVE contract：解析语义边，统一交给构造后的组合图校验
        live_rows = data.get("live_dependencies", [])
        # 没有 LIVE 时允许空列表；出现字段时必须仍是 JSON array。
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
        # endregion 2. LIVE contract 结束

    @property
    def digest(self) -> str:
        """返回恢复校验使用的稳定计划摘要。"""

        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """生成 digest、checkpoint 和 artifact 共用的 canonical mapping。"""

        payload: dict[str, Any] = {
            "goal": self.goal,
            "tasks": [_task_to_dict(task) for task in self.tasks],
        }
        # 空 V1 字段不落盘，确保 V0 计划摘要和检查点继续有效。
        if self.global_acceptance_criteria:
            payload["global_acceptance_criteria"] = list(
                self.global_acceptance_criteria
            )
        # LIVE 为空时同样省略字段，保持 HARD-only 计划的 canonical 形式。
        if self.live_dependencies:
            payload["live_dependencies"] = [
                dependency.to_dict() for dependency in self.live_dependencies
            ]
        return payload

    def live_dependencies_for(self, task_id: str) -> list[LiveDependency]:
        """返回以该 task 为 Consumer 的 inbound LIVE 边。"""

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
        """按 HARD + LIVE 组合图返回当前结果的稳定集成顺序。

        伪代码：构造每个候选的 HARD+LIVE 上游集合 -> 反复选出当前无未处理上游者
        -> 按计划顺序追加 -> 移除 ready，直到候选集合为空。
        """

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
        # 每轮剥离当前可集成层；输入 Plan 已校验，所以正常不会遇到环。
        while dependencies:
            ready = [
                task.id
                for task in self.tasks
                if task.id in dependencies
                and not (dependencies[task.id] & set(dependencies))
            ]
            # 防御性断言：若无 ready，说明组合图校验与此投影发生不一致。
            if not ready:  # pragma: no cover - FanoutPlan validation protects this
                raise AssertionError("validated fanout plan contains a cycle")
            ordered.extend(ready)
            # 移除本层后，下一轮下游 Task 才可能变为 ready。
            for task_id in ready:
                dependencies.pop(task_id)
        return ordered
# endregion 1. 唯一执行计划结束


# region 2. 结果与持久化 contract：Worker evidence → handoff/checkpoint/summary/finalizer
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
    # 失败结果没有显式 issue 时补一条稳定原因，避免下游看到空失败 Handoff。
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
# endregion 2. 结果与持久化 contract 结束


# region 3. Metrics 投影：区分当前 Run、恢复历史和完整 evidence chain
def aggregate_live_metrics(
    results: list[LiveSubagentResult],
    wall_time_ms: int,
    *,
    max_workers: int,
    finalizer_usage: dict[str, Any],
) -> dict[str, Any]:
    """区分本次消耗、恢复历史和完整证据链消耗。"""

    # region 1. 时间与数量：恢复 Worker 不进入本轮并发收益口径
    # wall time 只对应当前执行；历史恢复时长另列，避免制造虚假并发比率。
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
    # endregion 1. 时间与数量

    # region 2. Usage 三口径：current、resumed history、evidence chain
    # 每个 Usage key 使用同一公式，只有成本字段保留小数精度。
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
        # 成本保留六位小数；计数和 token/latency 统一投影为整数。
        if key == "estimated_cost_usd":
            metrics[key] = round(current_value, 6)
            metrics[f"resumed_{key}"] = round(resumed_worker_value, 6)
            metrics[f"evidence_chain_{key}"] = round(evidence_chain_value, 6)
        else:
            metrics[key] = int(current_value)
            metrics[f"resumed_{key}"] = int(resumed_worker_value)
            metrics[f"evidence_chain_{key}"] = int(evidence_chain_value)
    # endregion 2. Usage 三口径

    # region 3. Finalizer 单独可见：便于区分执行成本与最终验收成本
    metrics["finalizer_llm_calls"] = int(finalizer_usage.get("llm_calls") or 0)
    return metrics
    # endregion 3. Finalizer 单独可见
# endregion 3. Metrics 投影结束


# region 4. 输入规范化与组合图校验：所有外部字符串和依赖关系在执行前 fail closed
def _normalize_scope(value: Any) -> str:
    """把外部 scope 收敛为安全的 workspace 相对 POSIX 路径。"""

    text = str(value or "").strip().replace("\\", "/")
    # 连续移除无语义的 ``./``，让等价 scope 得到同一 canonical 形式。
    while text.startswith("./"):
        text = text[2:]
    path = PurePosixPath(text)
    # 空值、绝对路径和父目录逃逸都不能成为 Worker write scope。
    if not text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"write scope must be a relative workspace path: {value!r}")
    normalized = path.as_posix()
    # ``.`` 规范化后仍代表 workspace 根，不允许用它绕过粗粒度 scope 限制。
    if not normalized or normalized == ".":
        raise ValueError(f"write scope must be a relative workspace path: {value!r}")
    return normalized.rstrip("/") + ("/" if text.endswith("/") else "")


def _string_list(data: dict[str, Any], key: str) -> list[str]:
    """读取可选字符串数组，去空白并按首次出现顺序去重。"""

    values = data.get(key)
    # 缺失字段等价为空列表，避免调用方重复处理 None。
    if values is None:
        return []
    # 不接受单字符串，防止按字符迭代造成隐式错误。
    if not isinstance(values, list):
        raise ValueError(f"fanout task {key} must be a list")
    normalized = [str(value).strip() for value in values]
    # 任意空元素都会让依赖、scope 或工具名失去明确含义。
    if any(not value for value in normalized):
        raise ValueError(f"fanout task {key} entries must not be empty")
    return list(dict.fromkeys(normalized))


def _criteria_list(data: dict[str, Any], key: str) -> list[str]:
    criteria = _string_list(data, key)
    # 限制条数，控制 Planner/Finalizer Prompt 和 artifact 体积。
    if len(criteria) > 16:
        raise ValueError(f"{key} supports at most 16 entries")
    # 每条 criteria 也限制长度，避免单条内容绕过总量边界。
    if any(len(criterion) > 500 for criterion in criteria):
        raise ValueError(f"{key} entries support at most 500 characters")
    return criteria


def _dependency_list(data: dict[str, Any]) -> list[str]:
    """读取 HARD dependencies，保留顺序并拒绝重复或空引用。"""

    dependencies = data.get("depends_on")
    # 未声明 depends_on 表示没有 HARD 前置任务。
    if dependencies is None:
        return []
    # 依赖必须显式为数组，不接受逗号字符串等模糊输入。
    if not isinstance(dependencies, list):
        raise ValueError("fanout task depends_on must be a list")
    normalized = [str(value).strip() for value in dependencies]
    # 空 ID 无法解析到 Task，立即拒绝。
    if any(not value for value in normalized):
        raise ValueError("fanout task depends_on entries must not be empty")
    # 重复 HARD edge 没有额外语义，只会污染拓扑和 digest。
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
    # 空 criteria 不落盘，维持无该能力时的 canonical digest 兼容性。
    if task.acceptance_criteria:
        payload["acceptance_criteria"] = list(task.acceptance_criteria)
    return payload


def _validate_dependency_graph(
    tasks: list[SubagentTask],
    live_dependencies: list[LiveDependency],
) -> None:
    """一次性验证唯一 FanoutPlan 的 HARD、LIVE 和组合并发边界。

    伪代码：校验 Task/edge identity -> 拒绝未知和 HARD/LIVE 重叠
    -> 验证 HARD 图 -> 验证 HARD+LIVE 组合图 -> 检查 LIVE participant 写范围。
    """

    # region 1. Identity 与 edge：拒绝重复、未知、自引用和 HARD/LIVE 双重语义
    task_ids = [task.id for task in tasks]
    # 重复 ID 会让所有 dependency lookup 和 artifact 路径失去唯一性。
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("subagent task ids must be unique")
    known = set(task_ids)
    # 逐 Task 校验 HARD 自引用与重复 edge。
    for task in tasks:
        # 自依赖天然形成环，给出比通用 cycle 更直接的错误。
        if task.id in task.depends_on:
            raise ValueError(f"task {task.id} cannot depend on itself")
        # 同一上游只允许声明一次，保持 canonical graph。
        if len(task.depends_on) != len(set(task.depends_on)):
            raise ValueError(f"task {task.id} HARD dependencies must be unique")
    hard_pairs = {
        (dependency, task.id) for task in tasks for dependency in task.depends_on
    }
    live_identities = [
        (edge.producer_task_id, edge.target_task_id, edge.semantic_key)
        for edge in live_dependencies
    ]
    # Producer/Target/semantic_key 三元组必须唯一。
    if len(live_identities) != len(set(live_identities)):
        raise ValueError("LIVE semantic dependencies must be unique")
    live_pairs = {(producer, target) for producer, target, _ in live_identities}
    # V1 每个 Task pair 只允许一个 semantic key，避免多 mailbox barrier 复杂度。
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
    # HARD 或 LIVE 的任一 endpoint 未出现在 tasks 中都无法调度。
    if unknown:
        raise ValueError(f"unknown dependencies: {', '.join(unknown)}")
    overlap = sorted(hard_pairs & live_pairs)
    # 同一方向不能既 HARD 又 LIVE，否则启动语义互相矛盾。
    if overlap:
        producer, target = overlap[0]
        raise ValueError(
            f"dependency {producer} -> {target} cannot be both HARD and LIVE"
        )
    # endregion 1. Identity 与 edge 结束

    # region 2. 组合拓扑：分别复用 HARD 校验，再把 LIVE 边加入同一死锁检查
    # 复用 HARD 图校验，再校验组合图，统一阻断依赖环。
    build_execution_batches(tasks)
    upstream_by_task = {task.id: list(task.depends_on) for task in tasks}
    # 把 LIVE Producer 也作为集成拓扑上游，再复用同一个 cycle detector。
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
    # endregion 2. 组合拓扑结束

    # region 3. 并发写范围：所有 LIVE participant 必须能安全并行
    live_task_ids = {
        task_id
        for edge in live_dependencies
        for task_id in (edge.producer_task_id, edge.target_task_id)
    }
    conflicts = detect_write_scope_conflicts(
        [task for task in tasks if task.id in live_task_ids]
    )
    # LIVE Workers 会真实并发运行，因此声明 scope 重叠在执行前直接拒绝。
    if conflicts:
        raise ValueError(
            "LIVE workers require non-overlapping write scopes: "
            + conflicts[0].reason
        )
    # endregion 3. 并发写范围结束
# endregion 4. 输入规范化与组合图校验结束

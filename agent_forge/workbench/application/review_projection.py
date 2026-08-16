"""把 immutable evidence 投影成三条稳定的技术审阅路径。

本模块只读取版本化 contract 和 Runtime 已写入的 artifact。它不会补写 Run、推断
未观测状态，或把设计说明伪装成 Trace 事实。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_forge.workbench.domain import EvidenceSource


REVIEW_MANIFEST = Path("benchmarks/showcase/evidence-review-v1.json")


@dataclass(frozen=True)
class ReviewContract:
    """版本控制中的问题、机制和边界；不冒充单次 Run 的观测结果。"""

    title: str
    question: str
    mechanism: str
    boundary: str
    architecture_anchor: str


@dataclass(frozen=True)
class AuthorityFact:
    """一个 durable owner 当前回答的问题及其权威文件。"""

    owner: str
    question: str
    status: str
    value: str
    path: Path | None


@dataclass(frozen=True)
class InvariantFact:
    """只能由真实事件顺序支持的控制面不变量。"""

    statement: str
    observed: bool
    evidence: str


@dataclass(frozen=True)
class Lab1Review:
    contract: ReviewContract
    status: str
    state_sequence: tuple[str, ...]
    authorities: tuple[AuthorityFact, ...]
    invariants: tuple[InvariantFact, ...]
    observed_result: str
    evidence_revision: str


@dataclass(frozen=True)
class Lab2Task:
    task_id: str
    depends_on: tuple[str, ...]
    write_scope: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    touched_files: tuple[str, ...]
    status: str
    batch_index: int


@dataclass(frozen=True)
class Lab2Review:
    contract: ReviewContract
    status: str
    tasks: tuple[Lab2Task, ...]
    batches: tuple[tuple[str, ...], ...]
    conflicts: tuple[str, ...]
    final_decision: str
    finalizer_trace: Path | None
    observed_result: str


@dataclass(frozen=True)
class Mini50Case:
    case_id: str
    classification: str
    ordinal: int
    source_key: str


@dataclass(frozen=True)
class Mini50Review:
    contract: ReviewContract
    status: str
    planned: int
    resolved: int
    unresolved: int
    empty_patch: int
    initial_resolved: int
    initial_unresolved: int
    initial_empty_patch: int
    initial_infra_invalid: int
    completion_selected: tuple[int, ...]
    total_launches: int
    correctness_rerun: bool
    evaluated_revision: str
    current_revision: str
    representatives: tuple[Mini50Case, ...]
    attempts: tuple[str, ...]


def load_review_manifest(project_dir: Path) -> dict[str, Any]:
    """读取公开的审阅 contract；缺失或损坏时返回空映射并由调用方 fail closed。"""

    return _read_json(project_dir / REVIEW_MANIFEST)


def canonical_run_name(project_dir: Path, category: str) -> str:
    """返回 manifest 固定的本机 raw Run identity。"""

    source = _manifest_source(project_dir, category)
    return str(source.get("canonical_run") or "")


def build_lab1_review(project_dir: Path, source: EvidenceSource) -> Lab1Review:
    """从一个完整 governed Run 投影状态机、权威对象和三条不变量。"""

    contract = _contract(project_dir, "governed")
    root = source.primary_path.parent if source.primary_path else Path()
    manifest = _read_json(source.primary_path)
    checkpoints = list(
        (_read_json(path), path)
        for path in root.glob("phases/*/task_state/*.json")
    )
    checkpoints.sort(
        key=lambda item: float(item[0].get("created_at") or item[0].get("updated_at") or 0)
    )
    state_sequence = tuple(
        str(data.get("status") or "unknown") for data, _ in checkpoints
    )

    human_path, human = _latest_json(root / "human_input")
    approval_path, approval = _latest_json(root / "approvals")
    ledger_path, ledger = _latest_json(root / "operation_ledger")
    checkpoint_path = checkpoints[-1][1] if checkpoints else None
    final_trace = _last_path(root.glob("phases/*/trace.json"))
    events = _events(final_trace)

    human_loaded = _event_index(events, "human_input_response_loaded")
    approval_observed = _event_index(
        events, "human_approval", observation=str(approval.get("status") or "")
    )
    executing = _event_index(
        events, "operation_ledger", operation_status="executing"
    )
    tool_started = _event_index(events, "tool_execution_started", tool_call="replace_text")
    validation = _event_index(events, "validation_evidence")

    authorities = (
        AuthorityFact(
            "HumanInput",
            "用户回答了什么？",
            str(human.get("status") or "not_observed"),
            str(human.get("answer") or "未观测"),
            human_path,
        ),
        AuthorityFact(
            "Approval",
            "操作是否允许？",
            str(approval.get("status") or "not_observed"),
            str(approval.get("tool_name") or "未观测"),
            approval_path,
        ),
        AuthorityFact(
            "Operation Ledger",
            "副作用执行到哪？",
            str(ledger.get("status") or "not_observed"),
            " → ".join(str(item) for item in ledger.get("history") or ()) or "未观测",
            ledger_path,
        ),
        AuthorityFact(
            "Checkpoint",
            "当前 Run 生命周期状态？",
            state_sequence[-1] if state_sequence else "not_observed",
            " → ".join(state_sequence) or "未观测",
            checkpoint_path,
        ),
        AuthorityFact(
            "Trace",
            "实际发生了哪些 Event？",
            "observed" if events else "not_observed",
            f"{len(events)} events" if events else "未观测",
            final_trace,
        ),
    )
    invariants = (
        InvariantFact(
            "Human answer persisted before Resume",
            bool(human.get("status") == "responded" and human_loaded >= 0),
            "HumanInput=responded；continuation Trace 随后加载 response。",
        ),
        InvariantFact(
            "Approval persisted before side effect",
            bool(
                approval.get("status") == "approved"
                and approval_observed >= 0
                and tool_started > approval_observed
            ),
            "human_approval(approved) 位于 replace_text tool_execution_started 之前。",
        ),
        InvariantFact(
            "Ledger executing persisted before real side effect",
            bool(executing >= 0 and tool_started > executing),
            "operation_ledger(executing) 位于 replace_text tool_execution_started 之前。",
        ),
    )
    observed = (
        f"{state_sequence[-1] if state_sequence else manifest.get('status', 'unknown')}；"
        f"validation {'observed' if validation >= 0 else 'not observed'}；"
        f"{sum(item.observed for item in invariants)}/3 invariants observed"
    )
    return Lab1Review(
        contract=contract,
        status=str(manifest.get("status") or source.status),
        state_sequence=state_sequence,
        authorities=authorities,
        invariants=invariants,
        observed_result=observed,
        evidence_revision=_evidence_revision(final_trace),
    )


def build_lab2_review(project_dir: Path, source: EvidenceSource) -> Lab2Review:
    """读取 FanoutPlan 与 summary，保持设计门禁和观测结果来源分离。"""

    contract = _contract(project_dir, "orchestration")
    summary = _read_json(source.primary_path)
    run_root = source.primary_path.parent.parent if source.primary_path else Path()
    plan = _read_json(run_root / "fanout/fanout_plan.json")
    result_by_id = {
        str(item.get("task_id") or ""): item
        for item in summary.get("results") or ()
        if isinstance(item, dict)
    }
    tasks: list[Lab2Task] = []
    for task in plan.get("tasks") or ():
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "")
        result = result_by_id.get(task_id, {})
        tasks.append(
            Lab2Task(
                task_id=task_id,
                depends_on=_strings(task.get("depends_on")),
                write_scope=_strings(task.get("write_scope")),
                allowed_tools=_strings(task.get("allowed_tools")),
                touched_files=_strings(result.get("touched_files")),
                status=str(result.get("status") or "not_observed"),
                batch_index=int(result.get("batch_index") or 0),
            )
        )
    batches = tuple(
        tuple(str(task_id) for task_id in batch)
        for batch in summary.get("batches") or ()
        if isinstance(batch, list)
    )
    conflicts = tuple(str(item) for item in summary.get("conflicts") or ())
    finalizer = _safe_path(str(summary.get("finalizer_trace_path") or ""))
    completed = sum(task.status == "completed" for task in tasks)
    return Lab2Review(
        contract=contract,
        status=str(summary.get("status") or source.status),
        tasks=tuple(tasks),
        batches=batches,
        conflicts=conflicts,
        final_decision=str(summary.get("final_decision") or "not_observed"),
        finalizer_trace=finalizer,
        observed_result=(
            f"{completed}/{len(tasks)} tasks completed；"
            f"{len(conflicts)} conflicts；Finalizer "
            f"{summary.get('final_decision') or 'not observed'}"
        ),
    )


def build_mini50_review(
    project_dir: Path,
    source: EvidenceSource,
    sources: tuple[EvidenceSource, ...],
) -> Mini50Review:
    """把冻结结果收敛成唯一 50 条 terminal trajectory 与三个代表案例。"""

    contract = _contract(project_dir, "evaluation")
    manifest_source = _manifest_source(project_dir, "evaluation")
    result = _read_json(project_dir / str(manifest_source.get("result_artifact") or ""))
    completion = _read_json(
        project_dir / str(manifest_source.get("completion_artifact") or "")
    )
    initial = result.get("initial_observation")
    initial = initial if isinstance(initial, dict) else {}
    policy = completion.get("policy")
    policy = policy if isinstance(policy, dict) else {}
    source_by_case = {
        item.item_key: item.key
        for item in sources
        if item.category_key == "evaluation" and item.item_key != "overview"
    }
    classification_by_case: dict[str, str] = {}
    for classification, ids in (result.get("case_ids") or {}).items():
        for case_id in ids if isinstance(ids, list) else ():
            classification_by_case[str(case_id)] = str(classification)
    representatives: list[Mini50Case] = []
    for item in manifest_source.get("representative_cases") or ():
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("case_id") or "")
        source_key = source_by_case.get(case_id, "")
        ordinal = _case_ordinal(sources, case_id)
        representatives.append(
            Mini50Case(
                case_id=case_id,
                classification=classification_by_case.get(case_id, "unknown"),
                ordinal=ordinal,
                source_key=source_key,
            )
        )
    rounds = tuple(
        int(item.get("selected") or 0)
        for item in result.get("completion_rounds") or ()
        if isinstance(item, dict)
    )
    attempts = tuple(
        path.parent.name
        for path in sorted(
            (project_dir / ".agent_forge/runs/benchmarks").glob("**/campaign.json")
        )
        if "mini50" in path.parent.name
    )
    return Mini50Review(
        contract=contract,
        status=str(result.get("status") or source.status),
        planned=int(result.get("planned") or 0),
        resolved=int(result.get("official_resolved") or 0),
        unresolved=int(result.get("official_unresolved") or 0),
        empty_patch=int(result.get("agent_terminal_empty_patch") or 0),
        initial_resolved=int(initial.get("official_resolved") or 0),
        initial_unresolved=int(initial.get("official_unresolved") or 0),
        initial_empty_patch=int(initial.get("agent_terminal_empty_patch") or 0),
        initial_infra_invalid=int(initial.get("provider_invalid") or 0)
        + int(initial.get("external_interruption") or 0),
        completion_selected=rounds,
        total_launches=int(result.get("planned") or 0) + sum(rounds),
        correctness_rerun=bool(policy.get("correctness_rerun")),
        evaluated_revision=str(
            manifest_source.get("evaluated_revision")
            or (result.get("integrity") or {}).get("source_revision")
            or ""
        ),
        current_revision=_git_head(project_dir),
        representatives=tuple(representatives),
        attempts=attempts,
    )


def _manifest_source(project_dir: Path, category: str) -> dict[str, Any]:
    sources = load_review_manifest(project_dir).get("sources")
    sources = sources if isinstance(sources, dict) else {}
    value = sources.get(category)
    return value if isinstance(value, dict) else {}


def _contract(project_dir: Path, category: str) -> ReviewContract:
    data = _manifest_source(project_dir, category)
    return ReviewContract(
        title=str(data.get("title") or category),
        question=str(data.get("question") or "未登记"),
        mechanism=str(data.get("mechanism") or "未登记"),
        boundary=str(data.get("boundary") or "未登记"),
        architecture_anchor=str(data.get("architecture_anchor") or ""),
    )


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _latest_json(directory: Path) -> tuple[Path | None, dict[str, Any]]:
    paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not paths:
        return None, {}
    path = max(paths, key=lambda item: item.stat().st_mtime)
    return path, _read_json(path)


def _last_path(paths: Any) -> Path | None:
    available = [path for path in paths if path.is_file()]
    return max(available, key=lambda item: item.stat().st_mtime) if available else None


def _events(path: Path | None) -> list[dict[str, Any]]:
    value = _read_json(path).get("events")
    return [item for item in value or () if isinstance(item, dict)]


def _event_index(events: list[dict[str, Any]], event_type: str, **fields: str) -> int:
    for index, event in enumerate(events):
        if event.get("event_type") != event_type:
            continue
        if all(str(event.get(name) or "") == value for name, value in fields.items()):
            return index
    return -1


def _evidence_revision(trace_path: Path | None) -> str:
    for event in _events(trace_path):
        if event.get("event_type") != "execution_environment":
            continue
        environment = event.get("execution_environment")
        if isinstance(environment, dict):
            return str(environment.get("head_sha") or "")
    return ""


def _strings(value: Any) -> tuple[str, ...]:
    return tuple(str(item) for item in value or () if str(item))


def _safe_path(value: str) -> Path | None:
    path = Path(value) if value else None
    return path if path is not None and path.is_file() else None


def _case_ordinal(sources: tuple[EvidenceSource, ...], case_id: str) -> int:
    for source in sources:
        if source.category_key != "evaluation" or source.item_key != case_id:
            continue
        prefix = source.item_title.split(" · ", 1)[0]
        return int(prefix) if prefix.isdigit() else 0
    return 0


def _git_head(project_dir: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip() if result.returncode == 0 else ""

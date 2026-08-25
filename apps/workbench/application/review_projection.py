"""把 immutable evidence 投影成稳定的技术审阅路径。

本模块只读取版本化 contract 和 Runtime 已写入的 artifact。它不会补写 Run、推断
未观测状态，或把设计说明伪装成 Trace 事实。

系统角色：分别回答 Lab 1 的 durable governance、当前 Multi-Agent 协作和 Mini-50 的
能力评测问题，并把 contract 与 observed evidence 保持分栏。
输入：``EvidenceSource`` 与 manifest/raw artifact；输出：三个 typed Review model。
相邻边界：Evidence Catalog 负责定位文件；本 Application 负责事实投影；UI 只展示。

折叠导航：1 Review contract；2 Lab 1；3 Multi-Agent；4 Mini-50；5 helper。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.workbench.domain import EvidenceSource


# region 1. Review contract：问题/机制/边界来自版本控制，不冒充 Run observation
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
class FanoutTaskReview:
    task_id: str
    hard_dependencies: tuple[str, ...]
    live_dependencies: tuple[str, ...]
    write_scope: tuple[str, ...]
    status: str
    final_attempt: int | None
    attempt_statuses: tuple[str, ...]


@dataclass(frozen=True)
class FanoutReview:
    contract: ReviewContract
    status: str
    plan_digest: str
    tasks: tuple[FanoutTaskReview, ...]
    launch_waves: tuple[tuple[str, ...], ...]
    coordination_events: tuple[str, ...]
    candidate_gates: tuple[str, ...]
    final_decision: str
    observed_result: str


@dataclass(frozen=True)
class Mini50Case:
    case_id: str
    classification: str
    ordinal: int
    source_key: str
    role: str
    label: str
    selection_reason: str
    outcome: str
    patch_status: str
    key_turning_point: str
    success_reason: str
    root_cause: str
    what_to_inspect: str
    evidence_boundary: str
    provenance: tuple[tuple[str, str], ...]


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
# endregion 1. Review contract 结束


# region 2. Lab 1：从 durable owner 与事件顺序证明控制面不变量
def build_lab1_review(project_dir: Path, source: EvidenceSource) -> Lab1Review:
    """从一个完整 governed Run 投影状态机、权威对象和三条不变量。"""

    contract = _contract(project_dir, "governed")
    root = source.primary_path.parent if source.primary_path else Path()
    manifest = _read_json(source.primary_path)
    # Checkpoint sequence 与各控制面 Repository 分开读取，避免把 Trace 当成唯一权威。
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
    # 不变量只在事件索引和 durable 状态同时满足时 observed，缺一项即 false。
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
# endregion 2. Lab 1 结束


# region 3. Current Multi-Agent：冻结计划、Attempt、Task governance 与 LIVE 事实
def build_fanout_review(project_dir: Path, source: EvidenceSource) -> FanoutReview:
    """只读取 schema_version=3 的当前 Runtime 原生机制证据。"""

    contract = _contract(project_dir, "orchestration")
    evidence = _read_json(source.primary_path)
    plan = evidence.get("fanout_plan")
    plan = plan if isinstance(plan, dict) else {}
    if evidence.get("schema_version") == 3 and not plan and source.primary_path:
        plan = _read_json(source.primary_path.parent / "fanout_plan.json")
        evidence = {
            **evidence,
            "fanout_plan": plan,
            "finalizer_evidence": {
                "decision": evidence.get("final_decision") or "not_observed",
                "criterion_results": evidence.get("criterion_results") or [],
            },
        }
    task_rows = evidence.get("task_results")
    task_rows = task_rows if isinstance(task_rows, list) else []
    attempt_rows = evidence.get("attempt_results")
    attempt_rows = attempt_rows if isinstance(attempt_rows, list) else []
    task_result_by_id = {
        str(item.get("task_id") or ""): item
        for item in task_rows
        if isinstance(item, dict)
    }
    attempts_by_id: dict[str, list[dict[str, Any]]] = {}
    for item in attempt_rows:
        if isinstance(item, dict):
            attempts_by_id.setdefault(str(item.get("task_id") or ""), []).append(item)
    live_by_target: dict[str, list[str]] = {}
    for edge in plan.get("live_dependencies") or ():
        if isinstance(edge, dict):
            target = str(edge.get("target_task_id") or "")
            producer = str(edge.get("producer_task_id") or "")
            key = str(edge.get("semantic_key") or "")
            live_by_target.setdefault(target, []).append(f"{producer}:{key}")
    tasks: list[FanoutTaskReview] = []
    for task in plan.get("tasks") or ():
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "")
        result = task_result_by_id.get(task_id, {})
        attempts = sorted(
            attempts_by_id.get(task_id, ()),
            key=lambda item: int(item.get("attempt") or 0),
        )
        raw_final_attempt = result.get("final_attempt")
        tasks.append(
            FanoutTaskReview(
                task_id=task_id,
                hard_dependencies=_strings(task.get("depends_on")),
                live_dependencies=tuple(live_by_target.get(task_id, ())),
                write_scope=_strings(task.get("write_scope")),
                status=str(result.get("status") or "not_observed"),
                final_attempt=(int(raw_final_attempt) if raw_final_attempt is not None else None),
                attempt_statuses=tuple(
                    f"#{int(item.get('attempt') or 0)} {item.get('status') or 'unknown'}"
                    for item in attempts
                ),
            )
        )
    launch_waves = tuple(
        tuple(
            f"{item.get('task_id')}#{int(item.get('attempt') or 0)}"
            for item in wave
            if isinstance(item, dict)
        )
        for wave in evidence.get("launch_waves") or ()
        if isinstance(wave, list)
    )
    coordination_events = tuple(
        str((row.get("event") or {}).get("event_type"))
        for row in evidence.get("coordination_timeline") or ()
        if isinstance(row, dict) and isinstance(row.get("event"), dict)
    )
    candidate_gates = tuple(
        str(item.get("gate"))
        for item in evidence.get("candidate_gate_facts") or ()
        if isinstance(item, dict) and item.get("gate")
    )
    integrated = sum(task.status == "integrated" for task in tasks)
    finalizer = evidence.get("finalizer_evidence")
    finalizer = finalizer if isinstance(finalizer, dict) else {}
    return FanoutReview(
        contract=contract,
        status=str(evidence.get("status") or source.status),
        plan_digest=str(evidence.get("plan_digest") or ""),
        tasks=tuple(tasks),
        launch_waves=launch_waves,
        coordination_events=coordination_events,
        candidate_gates=candidate_gates,
        final_decision=str(finalizer.get("decision") or "not_observed"),
        observed_result=(
            f"{integrated}/{len(tasks)} tasks trusted integrated；"
            f"{len(attempt_rows)} real Worker Attempts；"
            f"Finalizer {finalizer.get('decision') or 'not observed'}"
        ),
    )
# endregion 3. Current Multi-Agent 结束


# region 4. Mini-50：固定分母、代表 Case 与 provenance closure
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
    # 只投影 manifest 明确选择的代表 Case，不从结果好坏反向挑样本。
    for item in manifest_source.get("representative_cases") or ():
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("case_id") or "")
        source_key = source_by_case.get(case_id, "")
        ordinal = _case_ordinal(sources, case_id)
        raw_provenance = item.get("provenance")
        provenance = (
            tuple((str(key), str(value)) for key, value in raw_provenance.items())
            if isinstance(raw_provenance, dict)
            else ()
        )
        representatives.append(
            Mini50Case(
                case_id=case_id,
                classification=classification_by_case.get(case_id, "unknown"),
                ordinal=ordinal,
                source_key=source_key,
                role=str(item.get("role") or ""),
                label=str(item.get("label") or ""),
                selection_reason=str(item.get("selection_reason") or ""),
                outcome=str(item.get("outcome") or ""),
                patch_status=str(item.get("patch_status") or ""),
                key_turning_point=str(item.get("key_turning_point") or ""),
                success_reason=str(item.get("success_reason") or ""),
                root_cause=str(item.get("root_cause") or ""),
                what_to_inspect=str(item.get("what_to_inspect") or ""),
                evidence_boundary=str(item.get("evidence_boundary") or ""),
                provenance=provenance,
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
        current_revision=current_git_revision(project_dir),
        representatives=tuple(representatives),
        attempts=attempts,
    )
# endregion 4. Mini-50 结束


# region 5. Manifest、Event 与 Revision helper
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


def current_git_revision(project_dir: Path) -> str:
    """返回 Workbench 当前 checkout；GitHub 深链不绑定默认分支名。"""

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_dir,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip() if result.returncode == 0 else ""
# endregion 5. Manifest、Event 与 Revision helper 结束

"""把人工反馈和既有运行证据投影成可追溯的文件 artifact。

系统角色：记录人工 outcome、连接 campaign 改进判断、导出分析 JSONL；本文件不会
重新判定 correctness，也不自动完成 secret/PII 审查。导出内容是否可外发由调用方负责。

折叠导航：1 请求契约；2 单次反馈；3 改进闭环；4 Dataset export；5 路径/JSON helper。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

FEEDBACK_OUTCOMES = {"accepted", "needs_work", "rejected"}
SCHEMA_VERSION = "agent-forge-eval-v1"
IMPROVEMENT_SCHEMA_VERSION = "agent-forge-improvement-v1"
IMPROVEMENT_DECISIONS = {"adopt", "iterate", "reject"}


# region 1. 请求契约：人工反馈与 campaign 改进判断的显式输入
# 核心数据：在既有运行证据上追加人工反馈的完整输入。
@dataclass(frozen=True, kw_only=True)
class FeedbackRequest:
    """目标 artifact、审核结论、标签、备注和审核人。"""

    target: str | Path
    outcome: str
    labels: tuple[str, ...] = ()
    note: str = ""
    reviewer: str = "human"


# 核心数据：把一次 Runtime 改动与前后评测证据连接起来。
@dataclass(frozen=True, kw_only=True)
class ImprovementRecordRequest:
    """改进假设、对照变体、人工判断和诚实声明边界。"""

    campaign_dir: str | Path
    observed_problem: str
    hypothesis: str
    change_ref: str
    decision: str
    decision_rationale: str
    claim_boundary: str
    control_variant: str = "minimal-control"
    treatment_variant: str = "governed-runtime"
    diagnosis_source: str = "maintainer_review"
    diagnosis_review_status: str = "reviewed"
    reviewer: str = "project-maintainer"
    diagnosis_finding: str = ""
    diagnosis_evidence: tuple[str, ...] = ()
# endregion 1. 请求契约结束


# region 2. 单次反馈：验证目标/outcome，再以同目录 replace 发布 feedback.json
# 主要入口：在现有 run/case artifact 上追加人工 outcome、label 和 note。
def record_feedback(request: FeedbackRequest) -> Path:
    """把人工 outcome、标签和备注挂接到指定运行证据。

    note/reviewer 是调用方提供的原文，不经过内容脱敏；该文件用于项目内审阅事实，
    不能仅因结构化写入就视为可公开数据。
    """

    target_path = Path(request.target)
    target_dir = target_path.parent if target_path.is_file() else target_path
    if not target_dir.exists() or not target_dir.is_dir():
        raise ValueError(f"feedback target is not a directory: {target_dir}")
    normalized_outcome = request.outcome.strip().lower()
    if normalized_outcome not in FEEDBACK_OUTCOMES:
        choices = ", ".join(sorted(FEEDBACK_OUTCOMES))
        raise ValueError(
            f"unsupported feedback outcome: {request.outcome}; choose one of {choices}"
        )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "outcome": normalized_outcome,
        "labels": _unique_strings(request.labels),
        "note": request.note.strip(),
        "reviewer": request.reviewer.strip() or "human",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = target_dir / "feedback.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)
    return path
# endregion 2. 单次反馈结束


# region 3. 改进闭环：只引用已发布 summary/manifest，不重算 benchmark
# 主要入口：从现有 campaign 事实生成一条可审计的改进闭环记录。
def write_improvement_record(request: ImprovementRecordRequest) -> Path:
    """只投影既有 benchmark 指标，不重新计算或拔高 correctness 结论。"""

    # region 1. 证据前置条件：改进记录只能引用已发布 campaign 事实
    campaign_dir = Path(request.campaign_dir)
    summary_path = campaign_dir / "summary.json"
    manifest_path = campaign_dir / "manifest.json"
    summary = _read_json(summary_path)
    manifest = _read_json(manifest_path)
    if not summary or not manifest:
        raise ValueError(
            "improvement record requires campaign summary.json and manifest.json"
        )
    # endregion 1. 证据前置条件结束

    # region 2. 决策与变体校验：拒绝不存在或不受支持的对照
    # decision 必须是预定义采纳动作，control/treatment 必须真实存在于 summary；
    # 本函数不允许调用方凭空提交一个没有配对指标支持的“改进”。
    decision = request.decision.strip().lower()
    if decision not in IMPROVEMENT_DECISIONS:
        choices = ", ".join(sorted(IMPROVEMENT_DECISIONS))
        raise ValueError(
            f"unsupported improvement decision: {request.decision}; "
            f"choose one of {choices}"
        )
    variants = summary.get("variants")
    if not isinstance(variants, dict):
        raise ValueError("campaign summary has no variant metrics")
    control = variants.get(request.control_variant)
    treatment = variants.get(request.treatment_variant)
    if not isinstance(control, dict) or not isinstance(treatment, dict):
        raise ValueError(
            "improvement record variants are absent from campaign summary: "
            f"{request.control_variant}, {request.treatment_variant}"
        )
    # endregion 2. 决策与变体校验结束

    # region 3. 审计载荷：保留来源、诊断、假设、前后指标和 claim boundary
    # payload 把“观察 -> 诊断 -> 假设 -> 改动 -> 指标差值 -> 人工决策”固化在一起；
    # delta 只做确定性算术，诊断和采纳理由仍明确标注来源与人工复核状态。
    config = manifest.get("config")
    config = config if isinstance(config, dict) else {}
    case_ids = [
        str(case_id)
        for case_id in config.get("case_ids") or []
        if str(case_id).strip()
    ]
    payload = {
        "schema_version": IMPROVEMENT_SCHEMA_VERSION,
        "record_id": f"{summary.get('campaign_id') or campaign_dir.name}-runtime-preset",
        "source_evidence": {
            "campaign_id": str(summary.get("campaign_id") or campaign_dir.name),
            "summary": summary_path.name,
            "manifest": manifest_path.name,
            "config_digest": str(summary.get("config_digest") or ""),
            "source_revision": str((summary.get("source") or {}).get("revision") or ""),
        },
        "observed_problem": request.observed_problem.strip(),
        "diagnosis": {
            "source": request.diagnosis_source.strip(),
            "review_status": request.diagnosis_review_status.strip(),
            "reviewer": request.reviewer.strip(),
            "finding": request.diagnosis_finding.strip(),
            "evidence": _unique_strings(request.diagnosis_evidence),
        },
        "hypothesis": request.hypothesis.strip(),
        "change": {
            "reference": request.change_ref.strip(),
            "control_variant": request.control_variant,
            "treatment_variant": request.treatment_variant,
            "comparison_factor": str(config.get("comparison_factor") or ""),
        },
        "regression_cases": case_ids,
        "before_after": {
            "control": _improvement_metrics(control),
            "treatment": _improvement_metrics(treatment),
            "delta": {
                "failed_tool_calls": _number(treatment, "failed_tool_calls")
                - _number(control, "failed_tool_calls"),
                "total_tokens": _number(treatment, "total_tokens")
                - _number(control, "total_tokens"),
                "estimated_cost_usd": round(
                    _number(treatment, "estimated_cost_usd")
                    - _number(control, "estimated_cost_usd"),
                    6,
                ),
                "execution_estimated_cost_usd": round(
                    _number(treatment, "execution_estimated_cost_usd")
                    - _number(control, "execution_estimated_cost_usd"),
                    6,
                ),
                "official_resolved": _number(treatment, "official_resolved")
                - _number(control, "official_resolved"),
                "patch_generated": _number(treatment, "patch_generated")
                - _number(control, "patch_generated"),
                "infrastructure_failures": _number(
                    treatment,
                    "infrastructure_failures",
                )
                - _number(control, "infrastructure_failures"),
            },
        },
        "decision": {
            "status": decision,
            "rationale": request.decision_rationale.strip(),
        },
        "claim_boundary": request.claim_boundary.strip(),
    }
    # endregion 3. 审计载荷结束

    # region 4. Replace 发布：避免读到半份 JSON，但不声明 fsync/OS-crash durability
    path = campaign_dir / "improvement_record.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path
    # endregion 4. Replace 发布结束
# endregion 3. 改进闭环结束


# region 4. Dataset export：去重 trace → 证据投影 → 可选反馈过滤 → JSONL
# 主要入口：将有反馈的 case evidence 导出为可追溯训练/分析 JSONL。
def export_feedback_dataset(
    targets: Iterable[str | Path],
    output_path: str | Path,
    *,
    require_feedback: bool = False,
    include_patch: bool = False,
) -> list[dict[str, Any]]:
    """导出固定字段的逐行反馈数据集，不把“字段受限”等同于“可公开”。

    默认记录仍可能包含 task、final_answer、路径、人工 note；``include_patch=True``
    还会加入 candidate diff 原文。调用方必须在外发或训练前单独做隐私与授权审查。

    伪代码：展开目标下 trace.json → 按 resolved path 去重 → 构造 derived record
    → 可选只保留已审核记录 → 写 JSONL。
    """

    records: list[dict[str, Any]] = []
    seen_traces: set[Path] = set()
    # 多个 target 可能覆盖同一 run；resolved trace identity 防止重复样本。
    for raw_target in targets:
        target = Path(raw_target)
        root = target.parent if target.is_file() else target
        for trace_path in _trace_paths(target):
            resolved_trace = trace_path.resolve()
            if resolved_trace in seen_traces:
                continue
            seen_traces.add(resolved_trace)
            record = _build_record(root, trace_path, include_patch=include_patch)
            if require_feedback and record["human_feedback"]["outcome"] == "unreviewed":
                continue
            records.append(record)

    # 一行一个完整 derived record；写出并不改变来源 trace/feedback/candidate artifact。
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records
    ]
    destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return records
# endregion 4. Dataset export 结束


# region 5. 单记录与路径 helper：只关联邻近 artifact，不猜测缺失结论
def _build_record(
    root: Path, trace_path: Path, *, include_patch: bool
) -> dict[str, Any]:
    """从一个 trace 关联最近 result/feedback/diff，并生成有 provenance 的投影。"""

    trace = _read_json(trace_path)
    events_value = trace.get("events")
    events: list[Any] = events_value if isinstance(events_value, list) else []
    context_files: list[str] = []
    tool_sequence: list[str] = []
    allowed_tools: list[str] = []
    hidden_tools: list[str] = []
    environment: dict[str, Any] = {}

    # 只提取 export contract 所需事件；未知事件仍留在原 Trace，不在这里解释。
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        if event_type == "context_assembly":
            context_value = event.get("context")
            context: dict[str, Any] = (
                context_value if isinstance(context_value, dict) else {}
            )
            context_files.extend(_strings(context.get("selected_files")))
            routing_value = context.get("tool_routing")
            routing: dict[str, Any] = (
                routing_value if isinstance(routing_value, dict) else {}
            )
            allowed_tools.extend(_strings(routing.get("allowed_tools")))
            hidden_tools.extend(_strings(routing.get("dropped_tools")))
        elif event_type == "action" and event.get("tool_call"):
            tool_sequence.append(str(event["tool_call"]))
        elif event_type == "execution_environment":
            raw_environment = event.get("execution_environment")
            if isinstance(raw_environment, dict):
                environment = {
                    key: raw_environment[key]
                    for key in ("mode", "head_sha", "dirty", "network_policy")
                    if key in raw_environment
                }

    instance_id = _instance_id(trace_path)
    result = _result_for_trace(root, trace_path, instance_id)
    feedback_path = _nearest_artifact(trace_path.parent, root, "feedback.json")
    feedback = _read_json(feedback_path) if feedback_path else {}
    if not feedback:
        feedback = {
            "schema_version": SCHEMA_VERSION,
            "outcome": "unreviewed",
            "labels": [],
            "note": "",
            "reviewer": "",
            "created_at": "",
        }

    candidate_diff_path = _nearest_artifact(
        trace_path.parent,
        root,
        "candidate_changes.diff",
    )
    candidate_diff_bytes = (
        candidate_diff_path.read_bytes()
        if candidate_diff_path and candidate_diff_path.exists()
        else b""
    )
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(trace.get("run_id") or ""),
        "instance_id": instance_id,
        "source": "benchmark" if instance_id else "repository_run",
        "task": str(trace.get("task") or ""),
        "stop_reason": str(trace.get("stop_reason") or ""),
        "final_answer": str(trace.get("final_answer") or ""),
        "result_status": str(result.get("status") or ""),
        "failure_class": str(result.get("failure_class") or ""),
        "evaluation_status": str(result.get("evaluation_status") or "not_evaluated"),
        "selected_context": _unique_strings(context_files),
        "tool_sequence": tool_sequence,
        "tool_policy": {
            "allowed": _unique_strings(allowed_tools),
            "hidden": _unique_strings(hidden_tools),
        },
        "environment": environment,
        "patch_chars": len(candidate_diff_bytes.decode("utf-8", errors="replace")),
        "candidate_diff_sha256": (
            hashlib.sha256(candidate_diff_bytes).hexdigest()
            if candidate_diff_bytes
            else ""
        ),
        "human_feedback": feedback,
        "provenance": {
            "trace": _relative_path(trace_path, root),
            "candidate_diff": (
                _relative_path(candidate_diff_path, root)
                if candidate_diff_path
                else ""
            ),
            "feedback": _relative_path(feedback_path, root) if feedback_path else "",
        },
    }
    if include_patch:
        # Patch 原文是显式 opt-in；默认只输出长度和 SHA-256 identity。
        record["candidate_diff"] = candidate_diff_bytes.decode(
            "utf-8",
            errors="replace",
        )
    return record


def _trace_paths(target: Path) -> list[Path]:
    if target.is_file():
        return [target] if target.name == "trace.json" else []
    if not target.exists():
        raise ValueError(f"evaluation target does not exist: {target}")
    return sorted(path for path in target.rglob("trace.json") if path.is_file())


def _result_for_trace(root: Path, trace_path: Path, instance_id: str) -> dict[str, Any]:
    for directory in _walk_to_root(trace_path.parent, root):
        results_path = directory / "results.json"
        payload = _read_json(results_path) if results_path.exists() else {}
        case_results = (
            payload.get("case_results") if isinstance(payload, dict) else None
        )
        if not isinstance(case_results, list):
            continue
        for result in case_results:
            if not isinstance(result, dict):
                continue
            if instance_id and str(result.get("instance_id") or "") == instance_id:
                return result
        if len(case_results) == 1:
            return case_results[0] if isinstance(case_results[0], dict) else {}
    return {}


def _instance_id(trace_path: Path) -> str:
    parts = trace_path.parts
    if "cases" not in parts:
        return ""
    index = parts.index("cases")
    return parts[index + 1] if index + 1 < len(parts) else ""


def _nearest_artifact(start: Path, root: Path, name: str) -> Path | None:
    for directory in _walk_to_root(start, root):
        candidate = directory / name
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _walk_to_root(start: Path, root: Path) -> list[Path]:
    start_resolved = start.resolve()
    root_resolved = root.resolve()
    try:
        start_resolved.relative_to(root_resolved)
    except ValueError:
        return [start_resolved]
    directories = []
    current = start_resolved
    while True:
        directories.append(current)
        if current == root_resolved:
            return directories
        current = current.parent


def _relative_path(path: Path | None, root: Path) -> str:
    if not path:
        return ""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return path.name


def _read_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item) for item in value if str(item).strip()]


def _unique_strings(values: Iterable[str]) -> list[str]:
    return list(
        dict.fromkeys(str(value).strip() for value in values if str(value).strip())
    )


def _improvement_metrics(variant: dict[str, Any]) -> dict[str, int | float]:
    """投影维护者复核改进决策所需的少量指标，避免复制整个 campaign summary。"""

    return {
        "planned": int(_number(variant, "planned")),
        "patch_generated": int(_number(variant, "patch_generated")),
        "official_evaluated": int(_number(variant, "official_evaluated")),
        "official_resolved": int(_number(variant, "official_resolved")),
        "infrastructure_failures": int(
            _number(variant, "infrastructure_failures")
        ),
        "tool_calls": int(_number(variant, "tool_calls")),
        "failed_tool_calls": int(_number(variant, "failed_tool_calls")),
        "total_tokens": int(_number(variant, "total_tokens")),
        "estimated_cost_usd": round(
            _number(variant, "estimated_cost_usd"),
            6,
        ),
        "execution_estimated_cost_usd": round(
            _number(variant, "execution_estimated_cost_usd"),
            6,
        ),
    }


def _number(mapping: dict[str, Any], key: str) -> float:
    value = mapping.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0
# endregion 5. 单记录与路径 helper 结束

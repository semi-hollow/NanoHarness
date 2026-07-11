from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


FEEDBACK_OUTCOMES = {"accepted", "needs_work", "rejected"}
SCHEMA_VERSION = "agent-forge-eval-v1"


def record_feedback(
    target: str | Path,
    *,
    outcome: str,
    labels: Iterable[str] = (),
    note: str = "",
    reviewer: str = "human",
) -> Path:
    """Persist a human judgment next to a run or benchmark case."""

    target_path = Path(target)
    target_dir = target_path.parent if target_path.is_file() else target_path
    if not target_dir.exists() or not target_dir.is_dir():
        raise ValueError(f"feedback target is not a directory: {target_dir}")
    normalized_outcome = outcome.strip().lower()
    if normalized_outcome not in FEEDBACK_OUTCOMES:
        choices = ", ".join(sorted(FEEDBACK_OUTCOMES))
        raise ValueError(f"unsupported feedback outcome: {outcome}; choose one of {choices}")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "outcome": normalized_outcome,
        "labels": _unique_strings(labels),
        "note": note.strip(),
        "reviewer": reviewer.strip() or "human",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    path = target_dir / "feedback.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def export_feedback_dataset(
    targets: Iterable[str | Path],
    output_path: str | Path,
    *,
    require_feedback: bool = False,
    include_patch: bool = False,
) -> list[dict[str, Any]]:
    """Export privacy-conscious run evidence as one JSON object per trace.

    Full tool arguments and observations are intentionally excluded. Candidate
    patch text is opt-in; the default record keeps only its size and digest.
    """

    records: list[dict[str, Any]] = []
    seen_traces: set[Path] = set()
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

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records]
    destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return records


def _build_record(root: Path, trace_path: Path, *, include_patch: bool) -> dict[str, Any]:
    trace = _read_json(trace_path)
    events = trace.get("events") if isinstance(trace.get("events"), list) else []
    context_files: list[str] = []
    tool_sequence: list[str] = []
    allowed_tools: list[str] = []
    hidden_tools: list[str] = []
    environment: dict[str, Any] = {}

    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        if event_type == "context_assembly":
            context = event.get("context") if isinstance(event.get("context"), dict) else {}
            context_files.extend(_strings(context.get("selected_files")))
            routing = context.get("tool_routing") if isinstance(context.get("tool_routing"), dict) else {}
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

    patch_path = _nearest_artifact(trace_path.parent, root, "patch.diff")
    patch_bytes = patch_path.read_bytes() if patch_path and patch_path.exists() else b""
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
        "patch_chars": len(patch_bytes.decode("utf-8", errors="replace")),
        "patch_sha256": hashlib.sha256(patch_bytes).hexdigest() if patch_bytes else "",
        "human_feedback": feedback,
        "provenance": {
            "trace": _relative_path(trace_path, root),
            "patch": _relative_path(patch_path, root) if patch_path else "",
            "feedback": _relative_path(feedback_path, root) if feedback_path else "",
        },
    }
    if include_patch:
        record["candidate_patch"] = patch_bytes.decode("utf-8", errors="replace")
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
        case_results = payload.get("case_results") if isinstance(payload, dict) else None
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
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))

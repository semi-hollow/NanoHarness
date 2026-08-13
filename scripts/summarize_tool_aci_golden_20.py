#!/usr/bin/env python3
"""汇总固定 Tool / ACI Golden-20 的成对 A/B 证据。

本脚本只读取 Agent artifacts、run 级 official aggregate 和 evaluator 保存的
``patch.diff``。它不会打开 sealed dataset、gold patch、逐测试状态或 evaluator 日志。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path("benchmarks/regression/tool-aci-golden-20-v1.json")
EDIT_TOOLS = {"replace_text", "create_file", "write_file"}
READ_TOOLS = {"read_file"}
SEARCH_TOOLS = {"grep_search", "find_files", "list_files"}
VALIDATION_TOOLS = {"python_validation", "run_command"}
BASELINE_PREREGISTRATION_COMMIT = "c5fb4b884019e7dabebe1b8b0afe1cec521e2f3b"
TREATMENT_COMMIT = "296000864d6a2c1476c28b790f030b0ffc4cca5b"
ROLLBACK_COMMIT = "a79d71051e0b968df81e5cc0f0851d434e89f358"
OFFICIAL_BUCKETS = {
    "resolved_ids": "resolved",
    "unresolved_ids": "unresolved",
    "empty_patch_ids": "empty",
    "error_ids": "error",
    "incomplete_ids": "incomplete",
}


class ReportRefused(ValueError):
    """证据不完整、漂移或相互矛盾时拒绝发布。"""


@dataclass(frozen=True)
class RunInput:
    variant: str
    shard: str
    run_dir: Path


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportRefused(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReportRefused(f"{label} must be a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_blob_sha256(project_root: Path, commit: str, path: str) -> str:
    """从冻结 commit 读取 Treatment blob，不依赖当前分支是否已经回滚。"""

    completed = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=project_root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ReportRefused(
            f"cannot read frozen treatment blob {commit}:{path}: {detail}"
        )
    return hashlib.sha256(completed.stdout).hexdigest()


def _ordered_ids_sha256(case_ids: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(case_ids).encode("utf-8")).hexdigest()


def _resolve(project_root: Path, raw: str | Path) -> Path:
    path = Path(raw)
    return (path if path.is_absolute() else project_root / path).resolve()


def _portable_path(project_root: Path, path: Path) -> str:
    """优先发布仓库相对路径，避免把本机用户名写入可跟踪证据。"""

    resolved = path.resolve()
    try:
        return str(resolved.relative_to(project_root.resolve()))
    except ValueError:
        return str(resolved)


def _only_file(paths: list[Path], label: str) -> Path:
    if len(paths) != 1:
        raise ReportRefused(f"expected exactly one {label}, found {len(paths)}")
    return paths[0]


def _safe_aggregate(
    run_dir: Path, run_id: str, expected_ids: list[str]
) -> tuple[Path, dict[str, str]]:
    candidates: list[Path] = []
    for path in sorted(run_dir.glob(f"*.{run_id}.json")):
        value = _read_json(path, "official aggregate candidate")
        if value.get("schema_version") == 2 and "resolved_ids" in value:
            candidates.append(path)
    path = _only_file(candidates, f"official aggregate for {run_id}")
    value = _read_json(path, "official aggregate")
    outcomes: dict[str, str] = {}
    for key, outcome in OFFICIAL_BUCKETS.items():
        items = value.get(key)
        if not isinstance(items, list) or any(
            not isinstance(item, str) for item in items
        ):
            raise ReportRefused(f"official aggregate {key} is invalid: {path}")
        for instance_id in items:
            if instance_id in outcomes:
                raise ReportRefused(
                    f"official aggregate has conflicting outcome: {instance_id}"
                )
            outcomes[instance_id] = outcome
    if set(outcomes) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(outcomes))
        extra = sorted(set(outcomes) - set(expected_ids))
        raise ReportRefused(
            f"official aggregate denominator drift: missing={missing} extra={extra}"
        )
    expected_counts = {
        "total_instances": len(expected_ids),
        "submitted_instances": len(expected_ids),
        "completed_instances": len(expected_ids),
        "resolved_instances": sum(item == "resolved" for item in outcomes.values()),
        "unresolved_instances": sum(item == "unresolved" for item in outcomes.values()),
        "empty_patch_instances": sum(item == "empty" for item in outcomes.values()),
        "error_instances": sum(item == "error" for item in outcomes.values()),
    }
    for key, expected in expected_counts.items():
        if value.get(key) != expected:
            raise ReportRefused(
                f"official aggregate {key} drift: {value.get(key)!r} != {expected}"
            )
    if any(item in {"error", "incomplete"} for item in outcomes.values()):
        raise ReportRefused(
            f"official aggregate contains infrastructure outcome: {path}"
        )
    return path, outcomes


def _load_predictions(path: Path, expected_ids: list[str]) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReportRefused(
                f"invalid prediction line {line_number}: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise ReportRefused(f"prediction line {line_number} is not an object")
        instance_id = str(row.get("instance_id") or "")
        patch = row.get("model_patch")
        if not instance_id or not isinstance(patch, str) or instance_id in result:
            raise ReportRefused(f"invalid or duplicate prediction line {line_number}")
        result[instance_id] = patch.encode("utf-8")
    if list(result) != expected_ids:
        raise ReportRefused("prediction order or denominator drift")
    return result


def _official_patch(run_dir: Path, run_id: str, instance_id: str) -> Path | None:
    root = run_dir / "logs" / "run_evaluation" / run_id
    candidates = sorted(root.glob(f"*/{instance_id}/patch.diff"))
    if not candidates:
        return None
    return _only_file(candidates, f"official patch for {instance_id}")


def _task_state(case_dir: Path) -> dict[str, Any]:
    path = _only_file(sorted((case_dir / "task_state").glob("*.json")), "task state")
    return _read_json(path, "task state")


def _provider_identity(trace: dict[str, Any]) -> dict[str, Any]:
    raw_calls = [
        event.get("model_usage")
        for event in trace.get("events", [])
        if isinstance(event, dict) and event.get("event_type") == "llm_call"
    ]
    if not raw_calls or any(not isinstance(call, dict) for call in raw_calls):
        raise ReportRefused("trace has no complete llm_call identity")
    calls: list[dict[str, Any]] = [call for call in raw_calls if isinstance(call, dict)]
    providers = sorted({str(call.get("provider") or "") for call in calls})
    models = sorted({str(call.get("model") or "") for call in calls})
    observed = sorted(
        {
            str(model)
            for call in calls
            for model in call.get("observed_models", [])
            if isinstance(model, str) and model
        }
    )
    if providers != ["opencode-go"] or models != ["deepseek-v4-flash"]:
        raise ReportRefused(f"provider/requested model drift: {providers}/{models}")
    if observed != ["deepseek-v4-flash"]:
        raise ReportRefused(f"provider-reported model drift: {observed}")
    if any(call.get("fallback_used") is not False for call in calls):
        raise ReportRefused("fallback was observed")
    attempts = [int(call.get("attempts") or 0) for call in calls]
    if any(value not in {1, 2} for value in attempts):
        raise ReportRefused(f"invalid model attempts: {attempts}")
    return {
        "llm_calls": len(calls),
        "providers": providers,
        "requested_models": models,
        "observed_models": observed,
        "fallback_calls": 0,
        "retried_calls": sum(value == 2 for value in attempts),
        "error_codes": sorted(
            {
                str(code)
                for call in calls
                for code in call.get("error_codes", [])
                if code
            }
        ),
    }


def _usage_metrics(usage: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    summary = usage.get("summary")
    efficiency = usage.get("tool_efficiency")
    if not isinstance(summary, dict) or not isinstance(efficiency, dict):
        raise ReportRefused("usage summary/tool_efficiency is missing")
    by_tool = efficiency.get("by_tool")
    if not isinstance(by_tool, dict):
        raise ReportRefused("usage tool breakdown is missing")

    chronological: list[str] = []
    for step in usage.get("steps", []):
        if not isinstance(step, dict):
            continue
        for action in step.get("actions", []):
            if isinstance(action, dict) and action.get("tool"):
                chronological.append(str(action["tool"]))
    try:
        first_edit_index = next(
            index for index, tool in enumerate(chronological) if tool in EDIT_TOOLS
        )
    except StopIteration:
        first_edit_index = -1
    before_edit = (
        chronological[:first_edit_index] if first_edit_index >= 0 else chronological
    )

    context_events = [
        event.get("context")
        for event in trace.get("events", [])
        if isinstance(event, dict) and event.get("event_type") == "context_assembly"
    ]
    repo_outline_contexts = sum(
        int(((context or {}).get("budget_breakdown") or {}).get("repo_outline") or 0)
        > 0
        for context in context_events
        if isinstance(context, dict)
    )
    available_find_files_contexts = sum(
        "find_files" in (context.get("available_tools") or [])
        for context in context_events
        if isinstance(context, dict)
    )
    head_tail_observations = sum(
        event.get("event_type") == "tool_observation"
        and isinstance(event.get("observation"), str)
        and "--- output head ---" in event["observation"]
        and "--- output tail ---" in event["observation"]
        for event in trace.get("events", [])
        if isinstance(event, dict)
    )

    def calls(tool: str) -> int:
        item = by_tool.get(tool)
        return int(item.get("calls") or 0) if isinstance(item, dict) else 0

    return {
        "llm_calls": int(summary.get("llm_calls") or 0),
        "prompt_tokens": int(summary.get("prompt_tokens") or 0),
        "completion_tokens": int(summary.get("completion_tokens") or 0),
        "total_tokens": int(summary.get("total_tokens") or 0),
        "estimated_cost_usd": float(summary.get("estimated_cost_usd") or 0.0),
        "llm_latency_ms": int(summary.get("llm_latency_ms") or 0),
        "steps": int(summary.get("steps") or 0),
        "tool_calls": int(summary.get("tool_calls") or 0),
        "failed_tool_calls": int(summary.get("failed_tool_calls") or 0),
        "failed_validations": int(summary.get("failed_validations") or 0),
        "grep_search_calls": calls("grep_search"),
        "find_files_calls": calls("find_files"),
        "list_files_calls": calls("list_files"),
        "search_calls": sum(calls(tool) for tool in SEARCH_TOOLS),
        "read_file_calls": calls("read_file"),
        "validation_calls": sum(calls(tool) for tool in VALIDATION_TOOLS),
        "edit_calls": sum(calls(tool) for tool in EDIT_TOOLS),
        "first_edit_call_index": first_edit_index + 1
        if first_edit_index >= 0
        else None,
        "tool_calls_before_first_edit": len(before_edit)
        if first_edit_index >= 0
        else None,
        "search_calls_before_first_edit": (
            sum(tool in SEARCH_TOOLS for tool in before_edit)
            if first_edit_index >= 0
            else None
        ),
        "read_calls_before_first_edit": (
            sum(tool in READ_TOOLS for tool in before_edit)
            if first_edit_index >= 0
            else None
        ),
        "repo_outline_contexts": repo_outline_contexts,
        "available_find_files_contexts": available_find_files_contexts,
        "head_tail_observations": head_tail_observations,
    }


def _load_run(
    project_root: Path, run: RunInput, expected_ids: list[str]
) -> dict[str, Any]:
    results_path = run.run_dir / "results.json"
    scorecard_path = run.run_dir / "scorecard.json"
    predictions_path = run.run_dir / "predictions.jsonl"
    for path in (results_path, scorecard_path, predictions_path):
        if not path.is_file():
            raise ReportRefused(f"missing finalized artifact: {path}")
    results = _read_json(results_path, "results")
    run_id = str(results.get("run_id") or "")
    if run_id != run.run_dir.name:
        raise ReportRefused(f"run id/path drift: {run_id} != {run.run_dir.name}")
    case_results = results.get("case_results")
    if not isinstance(case_results, list):
        raise ReportRefused("results case_results is missing")
    actual_ids = [
        str(item.get("instance_id") or "")
        for item in case_results
        if isinstance(item, dict)
    ]
    if actual_ids != expected_ids:
        raise ReportRefused(f"results case order drift for {run.variant}/{run.shard}")
    expected_profile = {
        "provider": "opencode-go",
        "model": "deepseek-v4-flash",
        "temperature": 0.0,
        "thinking_mode": "enabled",
        "reasoning_effort": "high",
        "max_steps": 128,
        "max_context_chars": 64000,
        "max_prompt_tokens": 131072,
        "reserved_output_tokens": 16384,
        "max_tool_calls_per_turn": 4,
        "model_request_max_attempts": 2,
        "model_request_timeout_seconds": 600,
        "tool_execution_timeout_seconds": 600,
        "timeout_seconds": 3600.0,
        "cost_budget_usd": None,
        "agent_mode": "single",
        "max_revision_rounds": 0,
        "tool_routing_mode": "task-aware",
        "memory_recall_limit": 0,
        "execution_mode": "worktree",
        "network_policy": "deny",
        "keep_worktree": False,
        "official_namespace": "swebench",
    }
    for key, expected in expected_profile.items():
        if results.get(key) != expected:
            raise ReportRefused(f"{run.variant}/{run.shard} profile drift: {key}")
    if results.get("skill_names") != ["swebench_repair"]:
        raise ReportRefused(f"{run.variant}/{run.shard} Skill drift")

    aggregate_path, outcomes = _safe_aggregate(run.run_dir, run_id, expected_ids)
    predictions = _load_predictions(predictions_path, expected_ids)
    cases: dict[str, Any] = {}
    for result in case_results:
        instance_id = str(result["instance_id"])
        case_dir = run.run_dir / "cases" / instance_id
        trace_path = case_dir / "trace.json"
        usage_path = case_dir / "usage.json"
        candidate_path = case_dir / "candidate_changes.diff"
        for path in (trace_path, usage_path, candidate_path):
            if not path.is_file():
                raise ReportRefused(f"missing case artifact: {path}")
        trace = _read_json(trace_path, "trace")
        usage = _read_json(usage_path, "usage")
        state = _task_state(case_dir)
        candidate = candidate_path.read_bytes()
        prediction = predictions[instance_id]
        if candidate != prediction:
            raise ReportRefused(f"candidate/prediction byte drift: {instance_id}")
        official_path = _official_patch(run.run_dir, run_id, instance_id)
        if outcomes[instance_id] in {"resolved", "unresolved"}:
            if official_path is None or official_path.read_bytes() != candidate:
                raise ReportRefused(
                    f"candidate/official patch byte drift: {instance_id}"
                )
        elif candidate or official_path is not None:
            raise ReportRefused(
                f"unexpected nonempty patch for {outcomes[instance_id]}: {instance_id}"
            )
        provider = _provider_identity(trace)
        metrics = _usage_metrics(usage, trace)
        if provider["llm_calls"] != metrics["llm_calls"]:
            raise ReportRefused(f"trace/usage llm call drift: {instance_id}")
        cases[instance_id] = {
            "instance_id": instance_id,
            "outcome": outcomes[instance_id],
            "resolved": outcomes[instance_id] == "resolved",
            "patch_generated": bool(candidate),
            "patch_bytes": len(candidate),
            "patch_sha256": hashlib.sha256(candidate).hexdigest(),
            "local_validation_status": str(
                result.get("local_validation_status") or "not_run"
            ),
            "task_status": str(state.get("status") or ""),
            "stop_reason": str(state.get("stop_reason") or ""),
            "provider_identity": provider,
            "metrics": metrics,
            "artifacts": {
                "trace_sha256": _sha256(trace_path),
                "usage_sha256": _sha256(usage_path),
                "candidate_sha256": _sha256(candidate_path),
                "official_patch_sha256": _sha256(official_path)
                if official_path
                else "",
            },
        }
    return {
        "variant": run.variant,
        "shard": run.shard,
        "run_id": run_id,
        "run_dir": _portable_path(project_root, run.run_dir),
        "case_ids": expected_ids,
        "outcomes": outcomes,
        "cases": cases,
        "artifacts": {
            "results_sha256": _sha256(results_path),
            "scorecard_sha256": _sha256(scorecard_path),
            "predictions_sha256": _sha256(predictions_path),
            "official_aggregate_sha256": _sha256(aggregate_path),
        },
    }


def _sum_metrics(cases: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(cases)
    keys = list(items[0]["metrics"]) if items else []
    result: dict[str, Any] = {}
    for key in keys:
        values = [item["metrics"].get(key) for item in items]
        present = [value for value in values if isinstance(value, (int, float))]
        total = round(sum(present), 6) if present else None
        if key in {
            "first_edit_call_index",
            "tool_calls_before_first_edit",
            "search_calls_before_first_edit",
            "read_calls_before_first_edit",
        }:
            result[f"{key}_sum"] = total
            result[f"{key}_mean"] = (
                round(float(total) / len(present), 6) if total is not None else None
            )
            result[f"{key}_observed_cases"] = len(present)
        else:
            result[key] = total
    return result


def _wilson(successes: int, total: int) -> list[float]:
    if total <= 0:
        return [0.0, 0.0]
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return [round(max(0.0, center - margin), 6), round(min(1.0, center + margin), 6)]


def _mcnemar_exact(gains: int, regressions: int) -> float:
    discordant = gains + regressions
    if discordant == 0:
        return 1.0
    lower = min(gains, regressions)
    probability = (
        2 * sum(math.comb(discordant, k) for k in range(lower + 1)) / (2**discordant)
    )
    return round(min(1.0, probability), 6)


def _decision(
    r0_resolved: int, r1_resolved: int, gains: int, regressions: int, activated: bool
) -> str:
    net = r1_resolved - r0_resolved
    if net >= 3 and regressions == 0 and activated:
        return "strong_positive"
    if net in {1, 2} or (net > 0 and regressions == 1):
        return "directional_only"
    if net == 0 and regressions == 0:
        return "efficiency_only_if_secondary_metrics_improve"
    if net < 0 or regressions > 0:
        return "reject"
    return "no_positive_result"


def _aggregate_variant(
    runs: list[dict[str, Any]], case_ids: list[str]
) -> dict[str, Any]:
    cases: dict[str, Any] = {}
    for run in runs:
        for instance_id, case in run["cases"].items():
            if instance_id in cases:
                raise ReportRefused(f"duplicate case across shards: {instance_id}")
            cases[instance_id] = case
    if list(case for case in case_ids if case in cases) != case_ids or set(
        cases
    ) != set(case_ids):
        raise ReportRefused("variant denominator/order drift")
    ordered = [cases[case_id] for case_id in case_ids]
    official = Counter(case["outcome"] for case in ordered)
    provider = {
        "llm_calls": sum(case["provider_identity"]["llm_calls"] for case in ordered),
        "fallback_calls": sum(
            case["provider_identity"]["fallback_calls"] for case in ordered
        ),
        "retried_calls": sum(
            case["provider_identity"]["retried_calls"] for case in ordered
        ),
        "error_codes": sorted(
            {
                code
                for case in ordered
                for code in case["provider_identity"]["error_codes"]
            }
        ),
    }
    return {
        "planned": len(case_ids),
        "terminal": len(ordered),
        "patch_generated": sum(case["patch_generated"] for case in ordered),
        "official": dict(sorted(official.items())),
        "official_resolved": official["resolved"],
        "official_resolved_rate": official["resolved"] / len(ordered),
        "official_resolved_wilson_95": _wilson(official["resolved"], len(ordered)),
        "local_validation_statuses": dict(
            sorted(Counter(case["local_validation_status"] for case in ordered).items())
        ),
        "task_statuses": dict(
            sorted(Counter(case["task_status"] for case in ordered).items())
        ),
        "provider": provider,
        "metrics": _sum_metrics(ordered),
        "cases": {case["instance_id"]: case for case in ordered},
        "runs": [
            {
                "shard": run["shard"],
                "run_id": run["run_id"],
                "run_dir": run["run_dir"],
                "case_ids": run["case_ids"],
                "artifacts": run["artifacts"],
            }
            for run in runs
        ],
    }


def build_report(
    project_root: Path, manifest_path: Path, r0_dirs: list[Path], r1_dirs: list[Path]
) -> dict[str, Any]:
    manifest = _read_json(manifest_path, "experiment manifest")
    case_ids = manifest.get("case_ids")
    if (
        not isinstance(case_ids, list)
        or len(case_ids) != 20
        or len(set(case_ids)) != 20
    ):
        raise ReportRefused("experiment manifest must contain 20 unique case ids")
    if manifest.get("ordered_case_ids_sha256") != _ordered_ids_sha256(case_ids):
        raise ReportRefused("experiment ordered case hash drift")
    if len(r0_dirs) != 4 or len(r1_dirs) != 4:
        raise ReportRefused("exactly four run directories are required per variant")
    runs: dict[str, list[dict[str, Any]]] = {"tool-r0": [], "tool-r1": []}
    for variant, directories in (("tool-r0", r0_dirs), ("tool-r1", r1_dirs)):
        for index, directory in enumerate(directories):
            start = index * 5
            runs[variant].append(
                _load_run(
                    project_root,
                    RunInput(
                        variant=variant, shard=chr(ord("a") + index), run_dir=directory
                    ),
                    case_ids[start : start + 5],
                )
            )
    r0 = _aggregate_variant(runs["tool-r0"], case_ids)
    r1 = _aggregate_variant(runs["tool-r1"], case_ids)
    transitions: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for index, instance_id in enumerate(case_ids):
        before = "resolved" if r0["cases"][instance_id]["resolved"] else "unresolved"
        after = "resolved" if r1["cases"][instance_id]["resolved"] else "unresolved"
        transition = f"{before}_to_{after}"
        counts[transition] += 1
        transitions.append(
            {
                "index": index + 1,
                "subset": "seen_regression" if index < 10 else "fresh_extension",
                "instance_id": instance_id,
                "r0": before,
                "r1": after,
                "transition": transition,
                "r0_patch_sha256": r0["cases"][instance_id]["patch_sha256"],
                "r1_patch_sha256": r1["cases"][instance_id]["patch_sha256"],
            }
        )
    gains = counts["unresolved_to_resolved"]
    regressions = counts["resolved_to_unresolved"]
    activation = {
        "find_files_exposed_contexts": r1["metrics"]["available_find_files_contexts"],
        "find_files_calls": r1["metrics"]["find_files_calls"],
        "repo_outline_contexts": r1["metrics"]["repo_outline_contexts"],
        "validation_head_tail_observations": r1["metrics"]["head_tail_observations"],
        "grep_search_calls": r1["metrics"]["grep_search_calls"],
    }
    activated = (
        activation["find_files_exposed_contexts"] > 0
        and activation["repo_outline_contexts"] > 0
        and activation["grep_search_calls"] > 0
    )
    subsets: dict[str, Any] = {}
    for name, selected in (
        ("seen_regression", case_ids[:10]),
        ("fresh_extension", case_ids[10:]),
    ):
        before = sum(r0["cases"][item]["resolved"] for item in selected)
        after = sum(r1["cases"][item]["resolved"] for item in selected)
        subsets[name] = {
            "planned": 10,
            "r0_resolved": before,
            "r1_resolved": after,
            "delta": after - before,
        }

    treatment_files = [
        "agent_forge/tools/grep.py",
        "agent_forge/tools/find_files.py",
        "agent_forge/context/repo_outline.py",
        "agent_forge/tools/output_window.py",
    ]
    return {
        "schema_version": 1,
        "artifact_type": "tool_aci_golden_20_paired_ab",
        "status": "completed",
        "experiment_id": manifest.get("set_id"),
        "claim": "fixed development-set paired Tool / ACI bundle comparison",
        "claim_limits": manifest.get("claim_limits"),
        "identity": {
            "manifest_path": _portable_path(project_root, manifest_path),
            "manifest_sha256": _sha256(manifest_path),
            "ordered_case_ids_sha256": manifest.get("ordered_case_ids_sha256"),
            "baseline_runtime_source_commit": manifest["experiment"][
                "tool_r0_source_commit"
            ],
            "baseline_preregistration_commit": BASELINE_PREREGISTRATION_COMMIT,
            "treatment_commit": TREATMENT_COMMIT,
            "rollback_commit": ROLLBACK_COMMIT,
            "fixed_profile": manifest.get("fixed_profile"),
            "treatment_file_sha256": {
                path: _git_blob_sha256(project_root, TREATMENT_COMMIT, path)
                for path in treatment_files
            },
        },
        "r0": r0,
        "r1": r1,
        "paired": {
            "transitions": transitions,
            "transition_counts": dict(sorted(counts.items())),
            "net_resolved_delta": r1["official_resolved"] - r0["official_resolved"],
            "percentage_point_delta": round(
                100 * (r1["official_resolved_rate"] - r0["official_resolved_rate"]), 3
            ),
            "mcnemar_exact_two_sided_p": _mcnemar_exact(gains, regressions),
            "decision": _decision(
                r0["official_resolved"],
                r1["official_resolved"],
                gains,
                regressions,
                activated,
            ),
            "subsets": subsets,
        },
        "treatment_activation": activation,
        "deferred": manifest["experiment"].get("deferred"),
    }


def _delta(before: Any, after: Any) -> str:
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        value = after - before
        return f"{value:+,.3f}" if isinstance(value, float) else f"{value:+,d}"
    return "-"


def render_markdown(report: dict[str, Any]) -> str:
    r0 = report["r0"]
    r1 = report["r1"]
    paired = report["paired"]
    metrics = [
        ("LLM calls", "llm_calls"),
        ("Total tokens", "total_tokens"),
        ("Estimated cost (USD)", "estimated_cost_usd"),
        ("Tool calls", "tool_calls"),
        ("Search calls", "search_calls"),
        ("grep_search", "grep_search_calls"),
        ("find_files", "find_files_calls"),
        ("read_file", "read_file_calls"),
        ("Validation calls", "validation_calls"),
        ("Failed tool calls", "failed_tool_calls"),
        ("Failed validations", "failed_validations"),
        ("Mean tool calls before first edit", "tool_calls_before_first_edit_mean"),
        ("Mean search calls before first edit", "search_calls_before_first_edit_mean"),
        ("Mean read calls before first edit", "read_calls_before_first_edit_mean"),
        ("Repo-outline contexts", "repo_outline_contexts"),
        ("Validation head/tail", "head_tail_observations"),
    ]
    lines = [
        "# Tool / ACI Golden-20 成对 A/B 实验报告",
        "",
        "## 结论",
        "",
        f"- Tool-R0：**{r0['official_resolved']}/20** official resolved。",
        f"- Tool-R1：**{r1['official_resolved']}/20** official resolved。",
        (
            "- Tool-R0 / R1 比例与 Wilson 95% 区间："
            f"**{100 * r0['official_resolved_rate']:.1f}% "
            f"[{100 * r0['official_resolved_wilson_95'][0]:.1f}%, "
            f"{100 * r0['official_resolved_wilson_95'][1]:.1f}%]** / "
            f"**{100 * r1['official_resolved_rate']:.1f}% "
            f"[{100 * r1['official_resolved_wilson_95'][0]:.1f}%, "
            f"{100 * r1['official_resolved_wilson_95'][1]:.1f}%]**。"
        ),
        f"- 净变化：**{paired['net_resolved_delta']:+d} Case / {paired['percentage_point_delta']:+.1f} 个百分点**。",
        f"- 逐题转换：`{paired['transition_counts']}`。",
        f"- 冻结门禁裁决：**{paired['decision']}**。",
        f"- McNemar exact two-sided p：`{paired['mcnemar_exact_two_sided_p']}`；20 题开发集只提供方向性证据，不作总体显著性或榜单声明。",
        "",
        "## 实验身份",
        "",
        f"- Baseline Runtime source：`{report['identity']['baseline_runtime_source_commit']}`",
        f"- Baseline preregistration：`{report['identity']['baseline_preregistration_commit']}`",
        f"- Treatment：`{report['identity']['treatment_commit']}`",
        f"- Reject rollback：`{report['identity']['rollback_commit']}`",
        f"- 模型：`{report['identity']['fixed_profile']['provider']}/{report['identity']['fixed_profile']['model']}`",
        "- 样本：固定 Golden-20；前 10 题为 seen regression，后 10 题为 outcome-blind fresh extension。",
        "- 口径：Pass@1、同 Case 顺序、同模型/预算/Runtime/evaluator；唯一主要变量为 Tool / repository context bundle。",
        "",
        "## 逐 Case 结果",
        "",
        "| # | subset | Case | R0 | R1 | transition |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    for item in paired["transitions"]:
        lines.append(
            f"| {item['index']} | `{item['subset']}` | `{item['instance_id']}` | "
            f"{item['r0']} | {item['r1']} | `{item['transition']}` |"
        )
    lines.extend(
        [
            "",
            "## 资源与工具行为",
            "",
            "| 指标 | R0 | R1 | Δ |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for label, key in metrics:
        before = r0["metrics"].get(key)
        after = r1["metrics"].get(key)
        lines.append(f"| {label} | {before} | {after} | {_delta(before, after)} |")
    activation = report["treatment_activation"]
    lines.extend(
        [
            "",
            "## Treatment 激活证据",
            "",
            f"- `grep_search`：Treatment 运行中调用 `{activation['grep_search_calls']}` 次；实现绑定到 `rg` 子进程。",
            f"- `find_files`：在 `{activation['find_files_exposed_contexts']}` 个 Context 中可见，实际调用 `{activation['find_files_calls']}` 次。",
            f"- `repo_outline`：进入 `{activation['repo_outline_contexts']}` 个 Context 组装事件；R0 为 `{r0['metrics']['repo_outline_contexts']}`。",
            f"- Validation head/tail：观察到 `{activation['validation_head_tail_observations']}` 次真实截断输出的 head+tail 保留。",
            "- `apply_patch` 按预注册协议 defer，没有进入 Treatment 变量。",
            "",
            "## 工程判断",
            "",
            "- 新能力被真实使用，且 `grep_search`、总搜索次数和首次编辑前搜索次数均下降；Validation head/tail 同时伴随 failed validations 下降。",
            "- 但这些过程指标没有转化为 official correctness 提升：1 个 Case 获益、2 个 Case 回归，净结果为 -1。",
            "- `repo_outline` 大量进入 Context，但 `read_file` 调用没有下降、总 token 略升；这只是后续拆分变量的线索，不构成本轮失败的单一因果解释。",
            "- 因此不合入本轮 bundle。若继续优化，应在新协议中逐组件验证，而不是对本轮 Golden-20 做结果驱动重跑。",
            "",
            "## 证据边界",
            "",
            "- 20/20 两侧均要求 candidate diff、prediction.model_patch 与 official evaluator patch 字节一致。",
            "- official outcome 只读取 run 级 safe aggregate；未读取 gold、逐测 tests_status、test_output 或 run_instance.log。",
            "- Agent generation 均为一次 Pass@1；R0 部分 official evaluator 基础设施失败只对同一冻结 prediction 做 evaluator-only 重试，没有重新生成 Patch。",
            "- Provider 内建 transport retry 按冻结配置最多 2 次；R0/R1 分别观察到 3/5 个 retried calls，均无 fallback。",
            "- 本实验评估整个 Tool / ACI bundle，不能把变化单独归因于其中某一个组件。",
            "- Golden-20 是固定开发集，不是 holdout，也不是 SWE-bench Verified 500 题榜单成绩。",
            "",
            "## 核心代码",
            "",
            "以下路径绑定到已拒绝的 Treatment commit；stable master 回滚后请在该 commit 中查看：",
            "",
            "- `agent_forge/tools/grep.py`",
            "- `agent_forge/tools/find_files.py`",
            "- `agent_forge/context/repo_outline.py`",
            "- `agent_forge/tools/output_window.py`",
            "- `agent_forge/tools/python_validation.py`",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--r0-run-dir", action="append", required=True)
    parser.add_argument("--r1-run-dir", action="append", required=True)
    parser.add_argument("--json-output", required=True)
    parser.add_argument("--markdown-output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest_path = _resolve(PROJECT_ROOT, args.manifest)
    report = build_report(
        PROJECT_ROOT,
        manifest_path,
        [_resolve(PROJECT_ROOT, item) for item in args.r0_run_dir],
        [_resolve(PROJECT_ROOT, item) for item in args.r1_run_dir],
    )
    json_output = _resolve(PROJECT_ROOT, args.json_output)
    markdown_output = _resolve(PROJECT_ROOT, args.markdown_output)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "completed",
                "json": str(json_output),
                "markdown": str(markdown_output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

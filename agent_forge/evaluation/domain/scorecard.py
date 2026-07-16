from __future__ import annotations

from collections import Counter
from typing import Any

OFFICIAL_EVALUATED = {"official_resolved", "official_eval_failed"}
NUMERIC_METRICS = (
    "llm_calls",
    "total_tokens",
    "estimated_cost_usd",
    "llm_latency_ms",
    "tool_calls",
    "failed_tool_calls",
    "compacted_context_turns",
    "context_overflow_recoveries",
    "memory_recalled",
    "tool_call_repairs",
    "bounded_tool_call_bursts",
)


def normalize_case(
    case: dict[str, Any],
    *,
    usage: dict[str, Any],
    environment: dict[str, Any],
) -> dict[str, Any]:

    probe_value = environment.get("probe")
    probe: dict[str, Any] = probe_value if isinstance(probe_value, dict) else {}
    summary_value = usage.get("summary")
    summary: dict[str, Any] = summary_value if isinstance(summary_value, dict) else {}
    official = str(
        case.get("official_evaluation_status")
        or _official_fallback(str(case.get("evaluation_status") or ""))
        or "not_evaluated"
    )
    local = str(case.get("local_validation_status") or "not_run")
    patch_chars = _int(case.get("patch_chars"))
    return {
        "instance_id": str(case.get("instance_id") or ""),
        "status": str(case.get("status") or ""),
        "patch_generated": patch_chars > 0 or bool(case.get("patch_generated")),
        "patch_chars": patch_chars,
        "local_validation_status": local,
        "local_verified": local == "passed",
        "official_evaluation_status": official,
        "official_evaluated": official in OFFICIAL_EVALUATED,
        "official_resolved": official == "official_resolved",
        "failure_class": str(case.get("failure_class") or "unclassified"),
        "execution_mode": str(probe.get("mode") or ""),
        "container_image_id": str(probe.get("container_image_id") or ""),
        "llm_calls": _int(summary.get("llm_calls")),
        "total_tokens": _int(summary.get("total_tokens")),
        "estimated_cost_usd": _float(summary.get("estimated_cost_usd")),
        "llm_latency_ms": _int(summary.get("llm_latency_ms")),
        "tool_calls": _int(summary.get("tool_calls")),
        "failed_tool_calls": _int(summary.get("failed_tool_calls")),
        "compacted_context_turns": _int(
            summary.get("compacted_context_turns")
        ),
        "context_overflow_recoveries": _int(
            summary.get("context_overflow_recoveries")
        ),
        "memory_recalled": _int(summary.get("memory_recalled")),
        "tool_call_repairs": _int(summary.get("tool_call_repairs")),
        "bounded_tool_call_bursts": _int(
            summary.get("bounded_tool_call_bursts")
        ),
        "_observed_models": _observed_models(usage),
        "_observed_container_image_id": str(probe.get("container_image_id") or ""),
    }


def build_scorecard(
    results: dict[str, Any],
    normalized_cases: list[dict[str, Any]],
) -> dict[str, Any]:

    cases = [dict(case) for case in normalized_cases]
    observed_models = sorted(
        {
            model
            for case in cases
            for model in case.pop("_observed_models", [])
            if model
        }
    )
    observed_container_image_ids = sorted(
        {
            image_id
            for case in cases
            for image_id in [case.pop("_observed_container_image_id", "")]
            if image_id
        }
    )
    return {
        "schema_version": 1,
        "metadata": _metadata(
            results,
            observed_models=observed_models,
            observed_container_image_ids=observed_container_image_ids,
        ),
        "metrics": aggregate_cases(cases),
        "cases": cases,
        "variants": aggregate_variants(results.get("variant_comparisons") or {}),
        "claim_boundary": {
            "candidate_patch": "non-empty diff only",
            "local_verified": "all recorded test-oriented validation evidence passed",
            "official_resolved": "official per-case SWE-bench report resolved=true",
        },
    }


def aggregate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:

    case_count = len(cases)
    patch_count = sum(bool(case.get("patch_generated")) for case in cases)
    local_count = sum(
        bool(case.get("local_verified"))
        or case.get("local_validation_status") == "passed"
        for case in cases
    )
    official_evaluated = sum(
        bool(case.get("official_evaluated"))
        or case.get("official_evaluation_status") in OFFICIAL_EVALUATED
        for case in cases
    )
    official_resolved = sum(
        bool(case.get("official_resolved"))
        or case.get("official_evaluation_status") == "official_resolved"
        for case in cases
    )
    metrics: dict[str, Any] = {
        "case_count": case_count,
        "patch_generated_count": patch_count,
        "patch_generated_rate": patch_count / case_count if case_count else None,
        "local_verified_count": local_count,
        "local_verified_rate": local_count / case_count if case_count else None,
        "official_evaluated_count": official_evaluated,
        "official_resolved_count": official_resolved,
        "official_resolved_rate": (
            official_resolved / official_evaluated if official_evaluated else None
        ),
        "failure_classes": dict(
            sorted(
                Counter(
                    str(case.get("failure_class") or "unclassified")
                    for case in cases
                ).items()
            )
        ),
        "official_statuses": dict(
            sorted(
                Counter(
                    str(case.get("official_evaluation_status") or "not_evaluated")
                    for case in cases
                ).items()
            )
        ),
    }
    for key in NUMERIC_METRICS:
        total = sum(_float(case.get(key)) for case in cases)
        metrics[key] = round(total, 6) if key == "estimated_cost_usd" else int(total)
    return metrics


def aggregate_variants(comparisons: object) -> dict[str, Any]:

    if not isinstance(comparisons, dict):
        return {}
    rows: dict[str, list[dict[str, Any]]] = {}
    for comparison in comparisons.values():
        variants = comparison.get("variants") if isinstance(comparison, dict) else None
        if not isinstance(variants, dict):
            continue
        for name, item in variants.items():
            if isinstance(item, dict):
                rows.setdefault(str(name), []).append(dict(item))
    return {name: aggregate_cases(items) for name, items in sorted(rows.items())}


def _metadata(
    results: dict[str, Any],
    *,
    observed_models: list[str],
    observed_container_image_ids: list[str],
) -> dict[str, Any]:
    return {
        "run_id": str(results.get("run_id") or ""),
        "dataset_name": str(results.get("dataset_name") or ""),
        "split": str(results.get("split") or ""),
        "provider": str(results.get("provider") or ""),
        "requested_model": str(results.get("model") or ""),
        "temperature": _float(results.get("temperature")),
        "observed_models": observed_models,
        "observed_container_image_ids": observed_container_image_ids,
        "agent_mode": str(results.get("agent_mode") or ""),
        "profile": str(results.get("profile") or ""),
        "max_steps": _int(results.get("max_steps")),
        "max_context_chars": _int(results.get("max_context_chars")),
        "max_revision_rounds": _int(results.get("max_revision_rounds")),
        "tool_routing_mode": str(results.get("tool_routing_mode") or "task-aware"),
        "skill_mode": str(results.get("skill_mode") or "auto"),
        "skill_names": sorted(str(item) for item in (results.get("skill_names") or [])),
        "skill_manifest_sha256": str(
            results.get("skill_manifest_sha256") or "builtins_only"
        ),
        "max_prompt_tokens": _int(results.get("max_prompt_tokens") or 32_768),
        "reserved_output_tokens": _int(
            results.get("reserved_output_tokens") or 4_096
        ),
        "max_tool_calls_per_turn": _int(
            results.get("max_tool_calls_per_turn") or 4
        ),
        "cost_budget_usd": (
            _float(results.get("cost_budget_usd"))
            if results.get("cost_budget_usd") is not None
            else None
        ),
        "timeout_seconds": _float(results.get("timeout_seconds") or 900.0),
        "memory_namespace": str(results.get("memory_namespace") or ""),
        "memory_recall_limit": _int(results.get("memory_recall_limit")),
        "memory_snapshot_sha256": str(
            results.get("memory_snapshot_sha256") or "disabled"
        ),
        "execution_mode": str(results.get("execution_mode") or "local"),
        "network_policy": str(results.get("network_policy") or "deny"),
        "keep_worktree": bool(results.get("keep_worktree")),
        "container_runtime": str(results.get("container_runtime") or "docker"),
        "container_image": str(results.get("container_image") or "python:3.11-slim"),
        "container_cpus": _float(results.get("container_cpus") or 1.0),
        "container_memory": str(results.get("container_memory") or "1g"),
        "container_pids_limit": _int(results.get("container_pids_limit") or 256),
        "container_read_only": bool(results.get("container_read_only", True)),
    }


def _observed_models(usage: dict[str, Any]) -> list[str]:
    models = set()
    steps = usage.get("steps")
    if not isinstance(steps, list):
        return []
    for step in steps:
        if not isinstance(step, dict):
            continue
        calls = step.get("llm_calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            if call.get("model"):
                models.add(str(call["model"]))
            if call.get("fallback_model"):
                models.add(str(call["fallback_model"]))
    return sorted(models)


def _official_fallback(evaluation_status: str) -> str:
    return evaluation_status if evaluation_status.startswith("official_") else ""


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0

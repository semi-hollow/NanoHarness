from __future__ import annotations

from .types import EvaluationComparison


def compare_runs(task_id: str, single: dict, multi: dict) -> EvaluationComparison:
    """Build a conservative comparison from two run/usage summaries."""

    comparison = EvaluationComparison(
        task_id=task_id,
        single_status=str(single.get("status") or single.get("stop_reason") or ""),
        multi_status=str(multi.get("status") or multi.get("stop_reason") or ""),
        single_patch_generated=bool(single.get("patch_generated") or single.get("patch_chars", 0)),
        multi_patch_generated=bool(multi.get("patch_generated") or multi.get("patch_chars", 0)),
        single_cost_usd=_float(single, "estimated_cost_usd"),
        multi_cost_usd=_float(multi, "estimated_cost_usd"),
        single_llm_calls=_int(single, "llm_calls"),
        multi_llm_calls=_int(multi, "llm_calls"),
        single_tool_calls=_int(single, "tool_calls"),
        multi_tool_calls=_int(multi, "tool_calls"),
        single_failed_tool_calls=_int(single, "failed_tool_calls"),
        multi_failed_tool_calls=_int(multi, "failed_tool_calls"),
        revision_rounds=_int(multi, "revision_rounds"),
        reviewer_findings=list(multi.get("reviewer_findings") or []),
        verifier_status=str(multi.get("verifier_status") or ""),
        failure_taxonomy=str(multi.get("failure_taxonomy") or single.get("failure_taxonomy") or ""),
    )
    comparison.recommendation = _recommend(comparison)
    return comparison


def _recommend(comparison: EvaluationComparison) -> str:
    """Return a non-hype recommendation from observed metrics."""

    if comparison.multi_status in {"passed", "patch_generated", "success"} and not comparison.single_patch_generated:
        return "multi-agent may be worth the extra cost for this task because single-agent did not produce a patch."
    if comparison.multi_failed_tool_calls > comparison.single_failed_tool_calls and comparison.multi_cost_usd > comparison.single_cost_usd:
        return "single-agent may be preferable here; multi-agent added cost and tool failures."
    return "insufficient evidence for a global claim; compare quality, cost, and failure mode case by case."


def _int(data: dict, key: str) -> int:
    """Read int-like metrics defensively."""

    try:
        return int(data.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _float(data: dict, key: str) -> float:
    """Read float-like metrics defensively."""

    try:
        return float(data.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0

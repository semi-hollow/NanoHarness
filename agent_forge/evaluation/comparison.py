from __future__ import annotations

from .types import EvaluationComparison


def compare_runs(task_id: str, single: dict, multi: dict) -> EvaluationComparison:
    """Build a conservative comparison from two run/usage summaries."""

    comparison = EvaluationComparison(
        task_id=task_id,
        single_status=str(single.get("status") or single.get("stop_reason") or ""),
        multi_status=str(multi.get("status") or multi.get("stop_reason") or ""),
        single_patch_generated=bool(single.get("patch_generated") or _int(single, "patch_chars") > 0),
        multi_patch_generated=bool(multi.get("patch_generated") or _int(multi, "patch_chars") > 0),
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


def compare_variants(task_id: str, variants: dict[str, dict]) -> dict:
    """Compare direct baseline, single agent, and governed agent without hype."""

    normalized = {name: _normalize_variant(data) for name, data in variants.items()}
    direct = normalized.get("direct_baseline", {})
    single = normalized.get("single_agent", {})
    governed = normalized.get("governed_agent", {})
    before_after = _before_after_summary(direct, single, governed)
    return {
        "task_id": task_id,
        "variants": normalized,
        "before_after_summary": before_after,
        "recommendation": _recommend_variants(direct, single, governed),
    }


def _normalize_variant(data: dict) -> dict:
    model_patch = data.get("model_patch")
    patch_generated = bool(
        data.get("patch_generated")
        or _int(data, "patch_chars") > 0
        or (isinstance(model_patch, str) and bool(model_patch.strip()))
    )
    return {
        "status": str(data.get("status") or data.get("stop_reason") or ""),
        "patch_generated": patch_generated,
        "verified": bool(data.get("verified") or data.get("local_verified") or data.get("official_resolved")),
        "failure_class": str(data.get("failure_class") or data.get("failure_taxonomy") or ""),
        "estimated_cost_usd": _float(data, "estimated_cost_usd"),
        "llm_calls": _int(data, "llm_calls"),
        "tool_calls": _int(data, "tool_calls"),
        "failed_tool_calls": _int(data, "failed_tool_calls"),
    }


def _before_after_summary(direct: dict, single: dict, governed: dict) -> str:
    if not direct:
        return "No direct baseline was recorded; compare agent variants only."
    if not direct.get("patch_generated") and single.get("patch_generated"):
        return "AgentLoop improved over one-shot baseline by reaching a candidate patch with tool-backed repository inspection."
    if single.get("failed_tool_calls", 0) > governed.get("failed_tool_calls", 0):
        return "Governed runtime reduced failed tool calls compared with the unguided single-agent loop."
    return "The comparison does not prove a quality improvement; read failure classes and cost before making a claim."


def _recommend_variants(direct: dict, single: dict, governed: dict) -> str:
    if not direct.get("patch_generated") and governed.get("patch_generated"):
        return "governed_agent is worth the added cost for this case because it produced a candidate patch where one-shot did not."
    if single and governed and governed.get("failed_tool_calls", 0) < single.get("failed_tool_calls", 0):
        return "governed_agent may be preferable because tool governance reduced failed tool calls."
    return "insufficient evidence for a global claim; compare success, observability, cost, and failure mode case by case."

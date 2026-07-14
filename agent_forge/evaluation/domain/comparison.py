from __future__ import annotations

from .models import EvaluationComparison


# PRIMARY ENTRYPOINT: compare one matched single-agent and multi-agent run.
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


# PRIMARY ENTRYPOINT: compare one-shot, single-agent, and governed variants.
def compare_variants(task_id: str, variants: dict[str, dict]) -> dict:
    """Compare direct baseline, single agent, and governed agent without hype."""

    normalized = {name: _normalize_variant(data) for name, data in variants.items()}
    direct = normalized.get("direct_baseline", {})
    agent_variants = {name: data for name, data in normalized.items() if name != "direct_baseline"}
    before_after = _before_after_summary(direct, agent_variants)
    return {
        "task_id": task_id,
        "variants": normalized,
        "before_after_summary": before_after,
        "recommendation": _recommend_variants(direct, agent_variants),
    }


def _normalize_variant(data: dict) -> dict:
    model_patch = data.get("model_patch")
    patch_generated = bool(
        data.get("patch_generated")
        or _int(data, "patch_chars") > 0
        or (isinstance(model_patch, str) and _looks_like_patch(model_patch))
    )
    local_status = str(data.get("local_validation_status") or "not_run")
    official_status = str(
        data.get("official_evaluation_status")
        or data.get("official_eval_status")
        or (data.get("evaluation_status") if str(data.get("evaluation_status") or "").startswith("official_") else "")
        or "not_evaluated"
    )
    local_verified = bool(data.get("local_verified")) or local_status == "passed"
    official_resolved = bool(data.get("official_resolved")) or official_status == "official_resolved"
    return {
        "status": str(data.get("status") or data.get("stop_reason") or ""),
        "patch_generated": patch_generated,
        "verified": bool(data.get("verified") or local_verified or official_resolved),
        "local_validation_status": local_status,
        "local_verified": local_verified,
        "official_evaluation_status": official_status,
        "official_resolved": official_resolved,
        "failure_class": str(data.get("failure_class") or data.get("failure_taxonomy") or ""),
        "estimated_cost_usd": _float(data, "estimated_cost_usd"),
        "llm_calls": _int(data, "llm_calls"),
        "total_tokens": _int(data, "total_tokens"),
        "llm_latency_ms": _int(data, "llm_latency_ms"),
        "tool_calls": _int(data, "tool_calls"),
        "failed_tool_calls": _int(data, "failed_tool_calls"),
    }


def _before_after_summary(direct: dict, agent_variants: dict[str, dict]) -> str:
    if not direct:
        return "No direct baseline was recorded; compare agent variants only."
    if not direct.get("patch_generated") and any(
        variant.get("patch_generated") for variant in agent_variants.values()
    ):
        return "AgentLoop improved over one-shot baseline by reaching a candidate patch with tool-backed repository inspection."
    single = agent_variants.get("single_agent", {})
    governed = agent_variants.get("governed_agent", {})
    if single and governed and single.get("failed_tool_calls", 0) > governed.get("failed_tool_calls", 0):
        return "Governed runtime reduced failed tool calls compared with the unguided single-agent loop."
    return "The comparison does not prove a quality improvement; read failure classes and cost before making a claim."


def _recommend_variants(direct: dict, agent_variants: dict[str, dict]) -> str:
    if not direct.get("patch_generated"):
        winner = _first_patch_variant(agent_variants)
        if winner:
            return (
                f"{winner} produced a candidate patch where direct_baseline did not; "
                "compare validation evidence before claiming solved."
            )
    single = agent_variants.get("single_agent", {})
    governed = agent_variants.get("governed_agent", {})
    if single and governed and governed.get("failed_tool_calls", 0) < single.get("failed_tool_calls", 0):
        return "governed_agent may be preferable because tool governance reduced failed tool calls."
    return "insufficient evidence for a global claim; compare success, observability, cost, and failure mode case by case."


def _first_patch_variant(agent_variants: dict[str, dict]) -> str:
    """Prefer governed when it really exists, otherwise name the actual variant."""

    if agent_variants.get("governed_agent", {}).get("patch_generated"):
        return "governed_agent"
    for name, variant in agent_variants.items():
        if variant.get("patch_generated"):
            return name
    return ""


def _looks_like_patch(text: str) -> bool:
    """Treat only patch-shaped direct baseline output as a generated patch."""

    stripped = text.strip()
    return stripped.startswith("diff --git ") or (
        stripped.startswith("--- ") and "\n+++ " in stripped
    )

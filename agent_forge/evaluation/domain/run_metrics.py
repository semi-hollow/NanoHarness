from __future__ import annotations

from typing import Any


def extract_run_metrics(
    result: dict[str, Any],
    usage: dict[str, Any] | None = None,
    multi_agent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Flatten one run into metrics accepted by compare_runs()."""

    usage = usage or {}
    multi_agent = multi_agent or {}
    totals = _metric_block(usage)
    role_results_value = multi_agent.get("role_results")
    role_results: list[Any] = role_results_value if isinstance(role_results_value, list) else []
    return {
        "status": str(result.get("status") or multi_agent.get("status") or "unavailable"),
        "patch_generated": bool(result.get("patch_generated") or _int(result, "patch_chars") > 0),
        "patch_chars": _int(result, "patch_chars"),
        "local_validation_status": str(result.get("local_validation_status") or "not_run"),
        "official_evaluation_status": str(
            result.get("official_evaluation_status") or result.get("evaluation_status") or "unavailable"
        ),
        "official_eval_status": str(
            result.get("official_evaluation_status") or result.get("evaluation_status") or "unavailable"
        ),
        "estimated_cost_usd": _float(totals, "estimated_cost_usd"),
        "llm_calls": _int(totals, "llm_calls"),
        "total_tokens": _int(totals, "total_tokens"),
        "llm_latency_ms": _int(totals, "llm_latency_ms"),
        "tool_calls": _int(totals, "tool_calls"),
        "failed_tool_calls": _int(totals, "failed_tool_calls"),
        "revision_rounds": _int(multi_agent, "revision_rounds"),
        "reviewer_findings": _reviewer_findings(role_results),
        "verifier_status": _verifier_status(role_results),
        "failure_taxonomy": str(result.get("failure_class") or multi_agent.get("status") or ""),
    }


def _metric_block(usage: dict[str, Any]) -> dict[str, Any]:
    """Support both current usage.json summary and older totals fixtures."""

    for key in ("summary", "totals"):
        block = usage.get(key)
        if isinstance(block, dict):
            return block
    return usage


def _reviewer_findings(role_results: list[Any]) -> list[str]:
    """Extract concise reviewer feedback from multi-agent role results."""

    findings: list[str] = []
    for item in role_results:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if "review" not in role.lower():
            continue
        text = str(item.get("final_answer") or "").strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) > 1:
            findings.append(lines[1])
        elif lines:
            findings.append(lines[0])
    return findings


def _verifier_status(role_results: list[Any]) -> str:
    """Return the latest verifier decision marker when available."""

    status = ""
    for item in role_results:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if "verifier" in role.lower() or "verify" in role.lower():
            status = str(item.get("decision") or item.get("status") or "")
    return status


def _int(data: dict[str, Any], key: str) -> int:
    try:
        return int(data.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _float(data: dict[str, Any], key: str) -> float:
    try:
        return float(data.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0

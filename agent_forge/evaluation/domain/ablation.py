from __future__ import annotations

from typing import Any

DELTA_METRICS = (
    "patch_generated_count",
    "local_verified_count",
    "official_evaluated_count",
    "official_resolved_count",
    "total_tokens",
    "estimated_cost_usd",
    "llm_latency_ms",
    "tool_calls",
    "failed_tool_calls",
)

# 主要入口：下方定义承接该模块的核心调用。
def compare_benchmark_scorecards(
    control: dict[str, Any],
    treatment: dict[str, Any],
    *,
    factor: str,
    control_label: str = "control",
    treatment_label: str = "treatment",
) -> dict[str, Any]:
    """在身份一致的前提下计算 control 与 treatment 的配对差异。"""

    checks = _validate_identity(control, treatment, factor=factor)
    control_cases = {str(case.get("instance_id") or ""): case for case in control.get("cases", [])}
    treatment_cases = {str(case.get("instance_id") or ""): case for case in treatment.get("cases", [])}
    if set(control_cases) != set(treatment_cases):
        missing_control = sorted(set(treatment_cases) - set(control_cases))
        missing_treatment = sorted(set(control_cases) - set(treatment_cases))
        raise ValueError(
            "case sets differ: "
            f"missing_from_control={missing_control} missing_from_treatment={missing_treatment}"
        )

    paired = [
        _paired_case(instance_id, control_cases[instance_id], treatment_cases[instance_id])
        for instance_id in sorted(control_cases)
    ]
    official_coverage = _official_coverage(paired)
    control_metrics = dict(control.get("metrics") or {})
    treatment_metrics = dict(treatment.get("metrics") or {})
    delta = {
        key: _numeric(treatment_metrics.get(key)) - _numeric(control_metrics.get(key))
        for key in DELTA_METRICS
    }
    if isinstance(delta["estimated_cost_usd"], float):
        delta["estimated_cost_usd"] = round(delta["estimated_cost_usd"], 6)
    delta["paired_official_evaluated_count"] = official_coverage["joint_count"]
    delta["paired_official_resolved_delta"] = sum(
        int(row["delta"]["official_resolved"])
        for row in paired
        if row["control"]["official_evaluated"] and row["treatment"]["official_evaluated"]
    )

    comparison = {
        "schema_version": 1,
        "factor": factor,
        "control": {
            "label": control_label,
            "metadata": control.get("metadata") or {},
            "metrics": control_metrics,
        },
        "treatment": {
            "label": treatment_label,
            "metadata": treatment.get("metadata") or {},
            "metrics": treatment_metrics,
        },
        "validity": {
            "comparable": True,
            "checks": checks,
            "official_coverage": official_coverage,
            "limitations": [
                "A single run per variant does not estimate stochastic variance.",
                "Candidate patch rate is edit reachability, not correctness.",
                "Official quality claims require per-case resolved/unresolved reports for both variants.",
            ],
        },
        "aggregate_delta": delta,
        "paired_cases": paired,
    }
    comparison["conclusion"] = _conclusion(comparison)
    return comparison


def _validate_identity(
    control: dict[str, Any],
    treatment: dict[str, Any],
    *,
    factor: str,
) -> list[dict[str, Any]]:
    control_meta = control.get("metadata") or {}
    treatment_meta = treatment.get("metadata") or {}
    checks = []
    for key in ("dataset_name", "split", "provider"):
        left = control_meta.get(key)
        right = treatment_meta.get(key)
        if left != right:
            raise ValueError(f"{key} differs: control={left!r} treatment={right!r}")
        checks.append({"field": key, "control": left, "treatment": right, "matched": True})
    left_model = _model_identity(control_meta)
    right_model = _model_identity(treatment_meta)
    if left_model != right_model:
        raise ValueError(f"model identity differs: control={left_model!r} treatment={right_model!r}")
    checks.append({"field": "model_identity", "control": left_model, "treatment": right_model, "matched": True})
    normalized_factor = factor.strip().lower().replace("_", "-")
    allowed_differences = {"tool_routing_mode"} if normalized_factor == "tool-routing" else set()
    if normalized_factor in {"execution-mode", "execution-environment", "isolation"}:
        allowed_differences.update(
            {
                "execution_mode",
                "network_policy",
                "keep_worktree",
                "container_runtime",
                "container_image",
                "container_cpus",
                "container_memory",
                "container_pids_limit",
                "container_read_only",
                "observed_container_image_ids",
            }
        )
    for key in (
        "agent_mode",
        "profile",
        "max_steps",
        "max_context_chars",
        "max_revision_rounds",
        "tool_routing_mode",
        "execution_mode",
        "network_policy",
        "keep_worktree",
        "container_runtime",
        "container_image",
        "container_cpus",
        "container_memory",
        "container_pids_limit",
        "container_read_only",
        "observed_container_image_ids",
    ):
        left = control_meta.get(key)
        right = treatment_meta.get(key)
        matched = left == right
        if not matched and key not in allowed_differences:
            raise ValueError(f"{key} differs outside declared factor {factor!r}: control={left!r} treatment={right!r}")
        checks.append(
            {
                "field": key,
                "control": left,
                "treatment": right,
                "matched": matched,
                "declared_factor_difference": not matched and key in allowed_differences,
            }
        )
    return checks


def _model_identity(metadata: dict[str, Any]) -> tuple[str, ...]:
    observed = tuple(sorted(str(item) for item in metadata.get("observed_models", []) if item))
    if observed:
        return observed
    requested = str(metadata.get("requested_model") or "")
    return (requested,) if requested else ()


def _paired_case(instance_id: str, control: dict[str, Any], treatment: dict[str, Any]) -> dict[str, Any]:
    control_evaluated = bool(control.get("official_evaluated"))
    treatment_evaluated = bool(treatment.get("official_evaluated"))
    if control_evaluated and treatment_evaluated and treatment.get("official_resolved") and not control.get("official_resolved"):
        outcome = "official_improved"
    elif control_evaluated and treatment_evaluated and control.get("official_resolved") and not treatment.get("official_resolved"):
        outcome = "official_regressed"
    elif treatment_evaluated and not control_evaluated:
        outcome = "official_evidence_added"
    elif control_evaluated and not treatment_evaluated:
        outcome = "official_evidence_missing"
    elif treatment.get("patch_generated") and not control.get("patch_generated"):
        outcome = "patch_reachability_improved"
    elif control.get("patch_generated") and not treatment.get("patch_generated"):
        outcome = "patch_reachability_regressed"
    else:
        outcome = "no_observed_quality_change"
    return {
        "instance_id": instance_id,
        "control": {**control, "official_evaluated": control_evaluated},
        "treatment": {**treatment, "official_evaluated": treatment_evaluated},
        "delta": {
            "patch_generated": int(bool(treatment.get("patch_generated"))) - int(bool(control.get("patch_generated"))),
            "local_verified": int(bool(treatment.get("local_verified"))) - int(bool(control.get("local_verified"))),
            "official_resolved": int(bool(treatment.get("official_resolved"))) - int(bool(control.get("official_resolved"))),
            "total_tokens": _numeric(treatment.get("total_tokens")) - _numeric(control.get("total_tokens")),
            "estimated_cost_usd": round(
                _numeric(treatment.get("estimated_cost_usd")) - _numeric(control.get("estimated_cost_usd")), 6
            ),
            "llm_latency_ms": _numeric(treatment.get("llm_latency_ms")) - _numeric(control.get("llm_latency_ms")),
            "failed_tool_calls": _numeric(treatment.get("failed_tool_calls")) - _numeric(control.get("failed_tool_calls")),
        },
        "outcome": outcome,
    }


def _conclusion(comparison: dict[str, Any]) -> str:
    delta = comparison.get("aggregate_delta") or {}
    coverage = comparison.get("validity", {}).get("official_coverage", {})
    official_delta = _numeric(delta.get("paired_official_resolved_delta"))
    joint_count = int(_numeric(delta.get("paired_official_evaluated_count")))
    if not coverage.get("matched", False):
        return (
            "Official correctness is not comparable across the full paired set because the per-case official "
            f"evaluation coverage differs; only {joint_count} case(s) have official evidence on both sides. "
            "Do not interpret the raw aggregate resolved-count delta as a runtime improvement."
        )
    if official_delta > 0:
        return (
            f"Treatment improved official resolved count by {int(official_delta)} paired case(s). "
            "Repeat the experiment before generalizing beyond this fixed set."
        )
    if official_delta < 0:
        return (
            f"Treatment regressed official resolved count by {abs(int(official_delta))} paired case(s); "
            "do not adopt it without diagnosing the affected cases."
        )
    if joint_count == 0:
        return (
            "The treatment changed runtime evidence, but correctness effect is unknown because the paired runs "
            "have no official resolved/unresolved evidence."
        )
    if _numeric(delta.get("failed_tool_calls")) < 0:
        return (
            "Treatment reduced failed tool calls with no observed official resolved-count change on this paired set. "
            "This supports an efficiency/reliability claim, not a global quality claim."
        )
    return "No official quality improvement was observed on this paired set; inspect case-level failures before adoption."


def _official_coverage(paired: list[dict[str, Any]]) -> dict[str, Any]:

    control_ids = [
        row["instance_id"] for row in paired if row["control"].get("official_evaluated")
    ]
    treatment_ids = [
        row["instance_id"] for row in paired if row["treatment"].get("official_evaluated")
    ]
    joint_ids = sorted(set(control_ids) & set(treatment_ids))
    return {
        "matched": set(control_ids) == set(treatment_ids),
        "control_count": len(control_ids),
        "treatment_count": len(treatment_ids),
        "joint_count": len(joint_ids),
        "control_only": sorted(set(control_ids) - set(treatment_ids)),
        "treatment_only": sorted(set(treatment_ids) - set(control_ids)),
    }


def _numeric(value: Any) -> int | float:
    try:
        return float(value or 0.0) if isinstance(value, float) else int(value or 0)
    except (TypeError, ValueError):
        return 0

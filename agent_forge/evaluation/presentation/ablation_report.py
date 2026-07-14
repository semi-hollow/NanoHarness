from __future__ import annotations

from typing import Any

from agent_forge.evaluation.domain.ablation import DELTA_METRICS


def render_ablation_report(comparison: dict[str, Any]) -> str:

    control = comparison["control"]
    treatment = comparison["treatment"]
    delta = comparison["aggregate_delta"]
    lines = [
        "# Paired Ablation Report",
        "",
        "## Experiment",
        "",
        f"- factor: `{comparison.get('factor', '')}`",
        f"- control: `{control.get('label', '')}` / `{control.get('metadata', {}).get('run_id', '')}`",
        f"- treatment: `{treatment.get('label', '')}` / `{treatment.get('metadata', {}).get('run_id', '')}`",
        f"- comparable: `{comparison.get('validity', {}).get('comparable', False)}`",
        "- official coverage matched: "
        f"`{comparison.get('validity', {}).get('official_coverage', {}).get('matched', False)}`",
        "- jointly officially evaluated cases: "
        f"`{comparison.get('validity', {}).get('official_coverage', {}).get('joint_count', 0)}`",
        "",
        "## Aggregate Delta",
        "",
        "| Metric | Control | Treatment | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for key in DELTA_METRICS:
        lines.append(
            f"| {key} | {control.get('metrics', {}).get(key, 0)} | "
            f"{treatment.get('metrics', {}).get(key, 0)} | {delta.get(key, 0)} |"
        )
    lines.extend(
        [
            "",
            "Official resolved-count deltas are comparable only when the same cases were officially evaluated "
            "on both sides. The conclusion below uses the jointly evaluated subset.",
            "",
            "## Paired Cases",
            "",
            "| instance | control official | treatment official | patch delta | failed-tool delta | outcome |",
            "| --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for row in comparison.get("paired_cases", []):
        lines.append(
            "| `{instance}` | `{control}` | `{treatment}` | {patch_delta} | {failed_delta} | `{outcome}` |".format(
                instance=row.get("instance_id", ""),
                control=row.get("control", {}).get(
                    "official_evaluation_status", "not_evaluated"
                ),
                treatment=row.get("treatment", {}).get(
                    "official_evaluation_status", "not_evaluated"
                ),
                patch_delta=row.get("delta", {}).get("patch_generated", 0),
                failed_delta=row.get("delta", {}).get("failed_tool_calls", 0),
                outcome=row.get("outcome", ""),
            )
        )
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            comparison.get("conclusion", ""),
            "",
            "## Limitations",
            "",
        ]
    )
    lines.extend(
        f"- {item}"
        for item in comparison.get("validity", {}).get("limitations", [])
    )
    lines.append("")
    return "\n".join(lines)

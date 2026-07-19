"""重复 benchmark campaign 的人类可读报告。"""

from __future__ import annotations

from typing import Any

from agent_forge.bench.domain.campaign import CampaignState


# 主要入口：把 campaign 状态与聚合事实渲染成 claim-safe Markdown。
def render_campaign_report(
    state: CampaignState,
    summary: dict[str, Any],
    *,
    public: bool = False,
) -> str:
    """报告只陈述已有证据；没有 official denominator 时不显示 0%。"""

    config = state.config
    source = state.source
    lines = [
        "# NanoHarness Benchmark Campaign",
        "",
        "## Experiment Identity",
        "",
        f"- campaign: `{state.campaign_id}`",
        f"- status: `{state.status}`",
        f"- source revision: `{source.get('revision') or 'unknown'}`",
        f"- source branch: `{source.get('branch') or 'unknown'}`",
        f"- dirty source allowed: `{bool(source.get('dirty'))}`",
        f"- dataset/split: `{_benchmark(config, 'dataset_name')}` / `{_benchmark(config, 'split')}`",
        f"- provider/model: `{_benchmark(config, 'provider')}` / `{_benchmark(config, 'model') or 'default'}`",
        f"- temperature: `{_benchmark(config, 'temperature')}`",
        f"- regression set: `{config.get('regression_set')}`",
        f"- cases: `{len(config.get('case_ids') or [])}`",
        f"- repetitions: `{config.get('repetitions')}`",
        f"- planned runs: `{summary.get('planned_runs')}`",
        f"- config digest: `{state.config_digest}`",
        "",
        "Variant order alternates by case and repetition to reduce systematic provider-time bias.",
        "Both variants use the same AgentLoop, model, task, sampling settings, budgets, safety policy and execution mode.",
        "",
        "## Runtime Presets",
        "",
        "| Variant | Tool visibility | Skills | Scope |",
        "| --- | --- | --- | --- |",
    ]
    for variant in config.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        lines.append(
            "| `{name}` | `{routing}` | `{skills}` | {description} |".format(
                name=variant.get("name") or "",
                routing=variant.get("tool_routing_mode") or "",
                skills=variant.get("skill_mode") or "",
                description=_table_cell(str(variant.get("description") or "")),
            )
        )
    lines.extend(
        [
            "",
            "> This is a multi-factor runtime-preset comparison, not a single-factor causal ablation.",
            "",
            "## Aggregate Evidence",
            "",
            "| Variant | Complete | Candidate patch | Local verified | Official resolved | Tokens | Cost USD | Failed tools |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for name, item in (summary.get("variants") or {}).items():
        official = _rate_with_denominator(
            item.get("official_resolved"),
            item.get("official_evaluated"),
        )
        lines.append(
            "| `{name}` | {completed}/{planned} | {patch}/{planned} | {local}/{planned} | {official} | {tokens} | {cost:.6f} | {failed} |".format(
                name=name,
                completed=item.get("completed", 0),
                planned=item.get("planned", 0),
                patch=item.get("patch_generated", 0),
                local=item.get("local_verified", 0),
                official=official,
                tokens=item.get("total_tokens", 0),
                cost=float(item.get("estimated_cost_usd") or 0.0),
                failed=item.get("failed_tool_calls", 0),
            )
        )
    paired = summary.get("paired_official") or {}
    wins = paired.get("wins") or {}
    lines.extend(
        [
            "",
            "## Paired Official Outcomes",
            "",
            f"- pairs with official outcomes on both variants: `{paired.get('evaluated_pairs', 0)}`",
            f"- wins: `{wins}`",
            f"- ties: `{paired.get('ties', 0)}`",
        ]
    )
    if not int(paired.get("evaluated_pairs") or 0):
        lines.append(
            "- No comparative correctness claim is available because no pair has official outcomes for both variants."
        )
    lines.extend(
        [
            "",
            "## Run Matrix",
            "",
            "| # | Case | Repeat | Variant | Run status | Patch | Local | Official | Failure class | Evidence |",
            "| ---: | --- | ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for record in sorted(state.records, key=lambda item: item.ordinal):
        evidence = record.evidence
        evidence_ref = (
            f"[scorecard](runs/{record.key}/scorecard.json)"
            if public and record.status == "completed"
            else (f"`{record.run_dir}`" if record.run_dir else "-")
        )
        lines.append(
            "| {ordinal} | `{case}` | {repeat} | `{variant}` | `{status}` | {patch} | `{local}` | `{official}` | `{failure}` | {evidence_ref} |".format(
                ordinal=record.ordinal,
                case=record.case_id,
                repeat=record.repetition,
                variant=record.variant,
                status=record.status,
                patch="yes" if evidence.get("patch_generated") else "no",
                local=evidence.get("local_validation_status") or "not_run",
                official=evidence.get("official_evaluation_status") or "not_evaluated",
                failure=evidence.get("failure_class") or "unclassified",
                evidence_ref=evidence_ref,
            )
        )
    lines.extend(
        [
            "",
            "## Claim Boundary",
            "",
            "- Candidate patch rate uses all planned runs and measures edit reachability, not correctness.",
            "- Official resolved rate uses only explicit resolved/unresolved official reports; missing evaluation is never converted to 0%.",
            "- The two presets intentionally differ in both tool routing and Skill activation, so this campaign evaluates the preset as a whole.",
            "- Smoke-5 is a mechanism regression set. It does not estimate SWE-bench Lite population performance or rank models.",
            "- Three repetitions expose obvious instability but are not enough for strong statistical significance claims.",
            "",
        ]
    )
    return "\n".join(lines)


def _benchmark(config: dict[str, Any], key: str) -> Any:
    benchmark = config.get("benchmark")
    return benchmark.get(key, "") if isinstance(benchmark, dict) else ""


def _rate_with_denominator(numerator: Any, denominator: Any) -> str:
    top = int(numerator or 0)
    bottom = int(denominator or 0)
    return f"{top}/{bottom} ({top / bottom:.1%})" if bottom else "not available"


def _table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()

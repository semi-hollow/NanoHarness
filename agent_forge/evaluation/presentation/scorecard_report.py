from __future__ import annotations

from typing import Any


def render_benchmark_scorecard(scorecard: dict[str, Any]) -> str:
    """Render a scorecard without discovering or interpreting artifacts."""

    metadata = scorecard.get("metadata") or {}
    metrics = scorecard.get("metrics") or {}
    official_rate = metrics.get("official_resolved_rate")
    official_text = "not available" if official_rate is None else f"{official_rate:.2%}"
    lines = [
        "# Benchmark Evidence Scorecard",
        "",
        "## Run Identity",
        "",
        f"- run_id: `{metadata.get('run_id', '')}`",
        f"- dataset/split: `{metadata.get('dataset_name', '')}` / `{metadata.get('split', '')}`",
        f"- provider/model: `{metadata.get('provider', '')}` / `{metadata.get('requested_model', '') or 'default'}`",
        f"- observed models: `{metadata.get('observed_models', [])}`",
        f"- tool routing: `{metadata.get('tool_routing_mode', '')}`",
        f"- execution/network: `{metadata.get('execution_mode', 'local')}` / "
        f"`{metadata.get('network_policy', 'deny')}`",
        f"- observed container image ids: `{metadata.get('observed_container_image_ids', [])}`",
        "",
        "## Aggregate Metrics",
        "",
        "| Metric | Value | Denominator |",
        "| --- | ---: | ---: |",
        f"| candidate patches | {metrics.get('patch_generated_count', 0)} | {metrics.get('case_count', 0)} |",
        f"| locally verified | {metrics.get('local_verified_count', 0)} | {metrics.get('case_count', 0)} |",
        f"| officially resolved | {metrics.get('official_resolved_count', 0)} | {metrics.get('official_evaluated_count', 0)} |",
        f"| official resolved rate | {official_text} | officially evaluated cases only |",
        f"| total tokens | {metrics.get('total_tokens', 0)} | provider usage |",
        f"| estimated cost USD | {metrics.get('estimated_cost_usd', 0.0):.6f} | provider usage |",
        f"| LLM latency ms | {metrics.get('llm_latency_ms', 0)} | provider calls |",
        f"| failed tool calls | {metrics.get('failed_tool_calls', 0)} | {metrics.get('tool_calls', 0)} tool calls |",
        "",
        "## Evidence Denominators",
        "",
        "- Candidate patch rate measures edit reachability, not correctness.",
        "- Local verification counts only explicit test-oriented validation evidence; compilation is excluded.",
        "- Official resolved rate uses only cases with explicit resolved/unresolved official reports.",
    ]
    if official_rate is None:
        lines.append(
            "- No official resolved rate is reported because zero cases have official resolved/unresolved evidence."
        )
    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| instance | patch | local | official | tokens | cost USD | failed tools | failure class |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for case in scorecard.get("cases", []):
        lines.append(
            "| `{instance_id}` | {patch} | `{local}` | `{official}` | {tokens} | {cost:.6f} | {failed} | `{failure}` |".format(
                instance_id=case.get("instance_id", ""),
                patch="yes" if case.get("patch_generated") else "no",
                local=case.get("local_validation_status", "not_run"),
                official=case.get("official_evaluation_status", "not_evaluated"),
                tokens=case.get("total_tokens", 0),
                cost=float(case.get("estimated_cost_usd") or 0.0),
                failed=case.get("failed_tool_calls", 0),
                failure=case.get("failure_class", "unclassified"),
            )
        )
    if not scorecard.get("cases"):
        lines.append(
            "| none | no | `not_run` | `not_evaluated` | 0 | 0.000000 | 0 | `unclassified` |"
        )
    lines.append("")
    return "\n".join(lines)

"""Live fanout summary 的 Markdown renderer。"""

from ..domain.live import LiveFanoutSummary


def render_live_fanout_report(summary: LiveFanoutSummary) -> str:
    """渲染当前消耗、恢复消耗、任务证据和 claim boundary。"""

    current_metric_keys = (
        "task_count",
        "completed_count",
        "max_workers",
        "wall_time_ms",
        "current_worker_duration_ms",
        "worker_time_to_wall_ratio",
        "llm_calls",
        "total_tokens",
        "estimated_cost_usd",
        "tool_calls",
        "failed_tool_calls",
        "finalizer_llm_calls",
    )
    recovery_metric_keys = (
        "resumed_count",
        "resumed_worker_duration_ms",
        "resumed_llm_calls",
        "resumed_total_tokens",
        "resumed_estimated_cost_usd",
        "evidence_chain_llm_calls",
        "evidence_chain_total_tokens",
        "evidence_chain_estimated_cost_usd",
    )
    lines = [
        "# Live Fanout Report",
        "",
        "## Run",
        "",
        f"- run_id: `{summary.run_id}`",
        f"- status: `{summary.status}`",
        f"- goal: {summary.goal}",
        f"- base_head: `{summary.base_head}`",
        f"- plan_digest: `{summary.plan_digest}`",
        f"- batches: `{summary.batches}`",
        f"- merged_task_ids: `{summary.merged_task_ids}`",
        f"- final_decision: `{summary.final_decision or 'not_run'}`",
        "",
        "## Current Run Metrics",
        "",
        "| metric | value |",
        "| --- | ---: |",
    ]
    lines.extend(
        f"| {key} | {summary.metrics.get(key, 0)} |"
        for key in current_metric_keys
    )
    lines.extend(
        [
            "",
            "## Recovery Accounting",
            "",
            "Recovered usage is historical; evidence-chain totals combine it with this run.",
            "",
            "| metric | value |",
            "| --- | ---: |",
        ]
    )
    lines.extend(
        f"| {key} | {summary.metrics.get(key, 0)} |"
        for key in recovery_metric_keys
    )
    lines.extend(
        [
            "",
            "## Tasks",
            "",
            "| task | status | batch | resumed | touched files | patch | trace |",
            "| --- | --- | ---: | --- | --- | --- | --- |",
        ]
    )
    for result in summary.results:
        lines.append(
            f"| `{result.task_id}` | `{result.status}` | {result.batch_index} | "
            f"`{result.resumed}` | `{result.touched_files}` | "
            f"[patch]({result.patch_path}) | [trace]({result.trace_path}) |"
        )
    lines.extend(["", "## Conflict Gate", ""])
    if summary.conflicts:
        lines.extend(
            f"- `{conflict.task_ids}`: {conflict.reason}"
            for conflict in summary.conflicts
        )
    else:
        lines.append(
            "- No static, dynamic, scope, or patch-apply conflict was observed."
        )
    lines.extend(
        [
            "",
            "## Finalizer",
            "",
            f"- trace: `{summary.finalizer_trace_path or 'not_run'}`",
            f"- usage: `{summary.finalizer_usage_path or 'not_run'}`",
            f"- llm_calls: `{summary.finalizer_usage_summary.get('llm_calls', 0)}`",
            "",
            "## Claim Boundary",
            "",
            "A merged patch and FanoutVerifier PASS are runtime evidence, not official benchmark resolution.",
            "",
        ]
    )
    return "\n".join(lines)

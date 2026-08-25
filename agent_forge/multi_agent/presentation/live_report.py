"""把 canonical ``FanoutSummary`` 投影成只读 Markdown 报告。"""

from ..domain.live import FanoutSummary


def render_fanout_report(summary: FanoutSummary) -> str:
    current_metric_keys = (
        "task_count",
        "attempt_count",
        "candidate_count",
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
    lines = [
        "# Fanout Report",
        "",
        "## Run",
        "",
        f"- schema_version: `{summary.schema_version}`",
        f"- run_id: `{summary.run_id}`",
        f"- status: `{summary.status}`",
        f"- goal: {summary.goal}",
        f"- base_head: `{summary.base_head}`",
        f"- plan_digest: `{summary.plan_digest}`",
        f"- launch_waves: `{summary.launch_waves}`",
        f"- merged_task_ids: `{summary.merged_task_ids}`",
        f"- integration_frontier_task_id: `{summary.integration_frontier_task_id or 'complete'}`",
        f"- final_decision: `{summary.final_decision or 'not_run'}`",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | ---: |",
    ]
    lines.extend(
        f"| {key} | {summary.metrics.get(key, 0)} |" for key in current_metric_keys
    )
    lines.extend(
        [
            "",
            "## Task Governance",
            "",
            "| task | status | failure kind | final attempt |",
            "| --- | --- | --- | ---: |",
        ]
    )
    for task_result in summary.task_results:
        lines.append(
            f"| `{task_result.task_id}` | `{task_result.status}` | "
            f"`{task_result.failure_kind or '-'}` | "
            f"{task_result.final_attempt if task_result.final_attempt is not None else '-'} |"
        )
    lines.extend(
        [
            "",
            "## Worker Attempts",
            "",
            "| task | attempt | status | launch wave | resumed | candidate | trace |",
            "| --- | ---: | --- | ---: | --- | --- | --- |",
        ]
    )
    for attempt_result in summary.attempt_results:
        lines.append(
            f"| `{attempt_result.task_id}` | {attempt_result.attempt} | "
            f"`{attempt_result.status}` | {attempt_result.launch_wave_index} | "
            f"`{attempt_result.resumed}` | "
            f"[diff]({attempt_result.candidate_diff_path}) | "
            f"[trace]({attempt_result.trace_path}) |"
        )
    lines.extend(["", "## Conflict Gate", ""])
    if summary.conflicts:
        lines.extend(
            f"- `{conflict.task_ids}`: {conflict.reason}"
            for conflict in summary.conflicts
        )
    else:
        lines.append("- No explicit FanoutConflict was emitted.")
    lines.extend(
        [
            "",
            "## Finalizer",
            "",
            f"- trace: `{summary.finalizer_trace_path or 'not_run'}`",
            f"- usage: `{summary.finalizer_usage_path or 'not_run'}`",
            "",
            "### Criterion Results",
            "",
        ]
    )
    if summary.criterion_results:
        lines.extend(
            f"- `{result.status}` {result.criterion}: {result.evidence}"
            for result in summary.criterion_results
        )
    else:
        lines.append("- No explicit acceptance criteria were evaluated.")
    lines.extend(
        [
            "",
            "## Claim Boundary",
            "",
            "An integrated diff and Finalizer PASS are runtime evidence, not official benchmark resolution.",
            "",
        ]
    )
    return "\n".join(lines)

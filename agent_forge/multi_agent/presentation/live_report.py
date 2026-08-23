"""把 ``LiveFanoutSummary`` 投影为只读、可审计的 Markdown 报告。

输入是 Coordinator 已经收口的结构化事实，输出是展示文本；本文件不重新判断
成功、冲突或恢复，也不读取 Worker 私有 Trace。折叠后按“Run/指标 -> Worker
证据 -> 治理结论”三块阅读即可。
"""

from ..domain.live import LiveFanoutSummary


def render_live_fanout_report(summary: LiveFanoutSummary) -> str:
    """渲染当前消耗、恢复消耗、任务证据和 Claim Boundary。

    伪代码：先写 Run/三种成本口径 -> 逐 Worker 写结果和 Handoff
    -> 展示恢复/冲突/Finalizer -> 以 Claim Boundary 收尾；不重新计算业务状态。
    """

    # region 1. Run 与指标：区分本轮成本、历史恢复成本和完整证据链成本
    current_metric_keys = (
        "task_count",
        "attempt_count",
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
        f"- effective_plan_digest: `{summary.effective_plan_digest or summary.plan_digest}`",
        f"- replan_round: `{summary.replan_round}`",
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
        f"| {key} | {summary.metrics.get(key, 0)} |" for key in current_metric_keys
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
        f"| {key} | {summary.metrics.get(key, 0)} |" for key in recovery_metric_keys
    )
    # endregion 1. Run 与指标

    # region 2. Worker 证据：展示任务结果、最小 Handoff 和恢复事实
    # 这里只使用 Summary 内已脱敏的投影，不展开私有 Conversation 或完整 Trace。
    lines.extend(
        [
            "",
            "## Tasks",
            "",
            "| task | attempt | status | batch | resumed | touched files | candidate diff | trace |",
            "| --- | ---: | --- | ---: | --- | --- | --- | --- |",
        ]
    )
    # 每个 Worker 一行，只引用 Summary 中已经收口的 canonical 路径和状态。
    for result in summary.results:
        lines.append(
            f"| `{result.task_id}` | {result.attempt} | `{result.status}` | "
            f"{result.batch_index} | "
            f"`{result.resumed}` | `{result.touched_files}` | "
            f"[candidate diff]({result.candidate_diff_path}) | "
            f"[trace]({result.trace_path}) |"
        )
    lines.extend(["", "## Handoffs", ""])
    # 逐结果展示最小 Handoff；完整 Conversation 和私有 Trace 不进入报告正文。
    for result in summary.results:
        # 没有 Handoff 的结果跳过，稍后用统一占位说明。
        if result.handoff is None:
            continue
        lines.append(
            f"- `{result.task_id}`: status=`{result.handoff.status}`, "
            f"validation=`{result.handoff.validation_evidence}`, "
            f"unresolved=`{result.handoff.unresolved_issues}`"
        )
    # 所有结果都无 Handoff 时显式呈现空状态，避免报告段落看似损坏。
    if not any(result.handoff is not None for result in summary.results):
        lines.append("- No WorkerHandoff was produced.")
    lines.extend(
        [
            "",
            "## Recovery",
            "",
            f"- replan_round: `{summary.replan_round}`",
            f"- attempt_count: `{len(summary.attempt_results)}`",
            f"- effective_plan_digest: `{summary.effective_plan_digest or summary.plan_digest}`",
            "",
            "## Conflict Gate",
            "",
        ]
    )
    # endregion 2. Worker 证据

    # region 3. 治理结论：冲突门、Finalizer 与可对外声称的边界
    # 有显式 FanoutConflict 时逐条列出；Worker status 中的失败仍由 Tasks 表保留。
    if summary.conflicts:
        lines.extend(
            f"- `{conflict.task_ids}`: {conflict.reason}"
            for conflict in summary.conflicts
        )
    else:
        lines.append(
            "- No explicit FanoutConflict record was emitted; inspect Worker status "
            "for scope or candidate failures."
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
            "### Criterion Results",
            "",
        ]
    )
    # Finalizer 返回逐条结果时展开；缺失时保留明确占位，不推测 PASS。
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
            "An integrated diff and Finalizer PASS are runtime evidence, "
            "not official benchmark resolution.",
            "",
        ]
    )
    return "\n".join(lines)
    # endregion 3. 治理结论

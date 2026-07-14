"""Usage read model 的 Markdown renderer。"""

from __future__ import annotations

from collections import Counter
from typing import Any


def render_usage_markdown(usage: dict[str, Any]) -> str:
    """把稳定 read model 呈现为工程报告，不重新推断运行结论。"""

    summary = usage["summary"]
    lines = [
        "# Agent Forge Usage Report",
        "",
        "## Run Summary",
        "",
        f"- run_id: `{usage.get('run_id', '')}`",
        f"- task: {usage.get('task', '')}",
        f"- stop_reason: `{usage.get('stop_reason', '')}`",
        f"- llm_calls: {summary['llm_calls']}",
        (
            f"- tokens: input={summary['prompt_tokens']} "
            f"output={summary['completion_tokens']} total={summary['total_tokens']}"
        ),
        (
            f"- cache: hit={summary['cache_hit_tokens']} "
            f"miss={summary['cache_miss_tokens']} "
            f"hit_rate={summary['cache_hit_rate']:.2%}"
        ),
        f"- estimated_cost_usd: ${summary['estimated_cost_usd']:.6f}",
        f"- llm_latency_ms: {summary['llm_latency_ms']}",
        (
            f"- tool_calls: {summary['tool_calls']} "
            f"failed={summary['failed_tool_calls']}"
        ),
        f"- hook_checks: {summary['hook_checks']}",
        f"- latest_task_status: `{summary['latest_task_status']}`",
        "",
        "## Runtime Control",
        "",
        (
            "- execution_environment: "
            f"`{usage['runtime_control'].get('execution_environment_mode', '')}`"
        ),
        f"- active_workspace: `{usage['runtime_control'].get('active_workspace', '')}`",
        f"- network_policy: `{usage['runtime_control'].get('network_policy', '')}`",
        f"- hook_decisions: {usage['runtime_control'].get('hook_decisions', {})}",
        f"- task_statuses: {usage['runtime_control'].get('task_statuses', {})}",
        "",
        "## Step Breakdown",
        "",
        (
            "| call | step | agent | provider/model | input | output | cache hit | "
            "cache miss | cost | latency ms | context chars | action summary |"
        ),
        "|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for step in usage["steps"]:
        context_chars = int((step.get("context") or {}).get("total_chars", 0) or 0)
        actions = _action_summary_text(step.get("actions", []))
        for call in step.get("llm_calls", []):
            model = f"{call.get('provider', '')}/{call.get('model', '')}".strip("/")
            lines.append(
                "|{call_index}|{step}|{agent}|{model}|{prompt}|{completion}|"
                "{hit}|{miss}|${cost:.6f}|{latency}|{context_chars}|{actions}|".format(
                    call_index=call["call_index"],
                    step=step["step"],
                    agent=step["agent"],
                    model=model,
                    prompt=call["prompt_tokens"],
                    completion=call["completion_tokens"],
                    hit=call["cache_hit_tokens"],
                    miss=call["cache_miss_tokens"],
                    cost=call["estimated_cost_usd"],
                    latency=call["latency_ms"],
                    context_chars=context_chars,
                    actions=actions or "none",
                )
            )
    if summary["llm_calls"] == 0:
        lines.append("|0|0|-|-|0|0|0|0|$0.000000|0|0|no llm call|")

    lines.extend(["", "## Context Breakdown", ""])
    lines.extend(
        _render_counter_table(
            usage["context_breakdown"]["section_chars"],
            "section",
            "chars",
        )
    )
    lines.extend(["", "## Tool Efficiency", ""])
    lines.append(
        "| tool | calls | success | failed | success rate | observation chars | duration ms |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for tool, data in usage["tool_efficiency"]["by_tool"].items():
        success_rate = data["success"] / data["calls"] if data["calls"] else 0.0
        lines.append(
            f"| {tool} | {data['calls']} | {data['success']} | {data['failed']} | "
            f"{success_rate:.2%} | {data['observation_chars']} | {data['duration_ms']} |"
        )
    if not usage["tool_efficiency"]["by_tool"]:
        lines.append("| none | 0 | 0 | 0 | 0.00% | 0 | 0 |")

    lines.extend(["", "## Evidence", ""])
    evidence_refs = usage.get("evidence_refs") or []
    lines.extend(
        [f"- `{evidence}`" for evidence in evidence_refs[:12]]
        if evidence_refs
        else ["- none"]
    )
    lines.extend(["", "## Optimization Notes", ""])
    lines.extend(f"- {note}" for note in usage.get("optimization_notes", []))
    return "\n".join(lines) + "\n"


def _action_summary_text(actions: list[dict[str, Any]]) -> str:
    counts = Counter(action.get("tool") or "unknown" for action in actions)
    return ", ".join(f"{tool}x{count}" for tool, count in sorted(counts.items()))


def _render_counter_table(
    counter: dict[str, int],
    key_label: str,
    value_label: str,
) -> list[str]:
    lines = [
        f"| {key_label} | {value_label} | est tokens |",
        "|---|---:|---:|",
    ]
    for key, value in sorted(counter.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {key} | {value} | {_chars_to_tokens(value)} |")
    if not counter:
        lines.append("| none | 0 | 0 |")
    return lines


def _chars_to_tokens(chars: int) -> int:
    return max(0, int(round(chars / 4)))


__all__ = ["render_usage_markdown"]

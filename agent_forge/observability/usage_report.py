"""Derived usage and efficiency reports from a raw trace.

The trace is the source of truth; this module is a read model. It answers the
questions that are hard to see in raw event JSON: how many model calls happened,
which step spent tokens, where cache hits appeared, how much context was sent,
which tools failed, and what runtime controls fired.

If removed:
    The project would still run, but it would lose the quantitative evidence
    needed for cost, latency, context-quality, and tool-efficiency discussions.
"""

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def write_usage_artifacts(trace_path: str | Path, output_dir: str | Path | None = None) -> tuple[Path, Path]:
    """Write machine-readable usage.json and human-readable usage_report.md.

    Trace is the append-only source of truth. Usage artifacts are derived views
    for cost and optimization conversations: run totals, step breakdown,
    context composition, and tool efficiency.
    """

    trace_file = Path(trace_path)
    trace = json.loads(trace_file.read_text(encoding="utf-8"))
    usage = build_usage_report(trace)
    usage_json, usage_md = usage_artifact_paths(trace_file, output_dir)
    usage_json.parent.mkdir(parents=True, exist_ok=True)
    usage_json.write_text(json.dumps(usage, ensure_ascii=False, indent=2), encoding="utf-8")
    usage_md.write_text(render_usage_markdown(usage), encoding="utf-8")
    return usage_json, usage_md


def usage_artifact_paths(trace_file: Path, output_dir: str | Path | None = None) -> tuple[Path, Path]:
    """Choose stable artifact names for session and ad-hoc trace runs."""

    target_dir = Path(output_dir) if output_dir else trace_file.parent
    if trace_file.name == "trace.json":
        return target_dir / "usage.json", target_dir / "usage_report.md"
    return (
        target_dir / f"{trace_file.stem}.usage.json",
        target_dir / f"{trace_file.stem}.usage_report.md",
    )


def build_usage_report(trace: dict[str, Any]) -> dict[str, Any]:
    """Aggregate trace events by run, step, context section, and tool result."""

    events = trace.get("events", [])
    steps: dict[tuple[int, str], dict[str, Any]] = {}
    llm_call_index = 0
    evidence_refs: list[str] = []

    def step_entry(event: dict[str, Any]) -> dict[str, Any]:
        """Return the mutable aggregate row for one step and agent."""

        key = (int(event.get("step", 0) or 0), str(event.get("agent_name") or "agent"))
        if key not in steps:
            steps[key] = {
                "step": key[0],
                "agent": key[1],
                "llm_calls": [],
                "context": {},
                "actions": [],
                "recoveries": [],
                "hook_checks": [],
                "task_states": [],
                "runtime": {},
                "permissions": {"allow": 0, "ask": 0, "deny": 0},
                "guardrail_blocks": 0,
                "errors": [],
            }
        return steps[key]

    for event in events:
        entry = step_entry(event)
        event_type = event.get("event_type")

        if event_type == "context_assembly":
            context = event.get("context") or {}
            entry["context"] = _context_summary(context)

        elif event_type == "llm_call":
            llm_call_index += 1
            entry["llm_calls"].append(_llm_call_summary(event, llm_call_index, trace.get("run_id", "")))

        elif event_type == "action":
            entry["actions"].append(
                {
                    "tool": event.get("tool_call", ""),
                    "arguments_keys": sorted((event.get("tool_arguments") or {}).keys()),
                    "success": None,
                    "observation_chars": 0,
                    "duration_ms": 0,
                }
            )

        elif event_type == "tool_observation":
            action = _last_action_without_observation(entry)
            if action is None:
                action = {
                    "tool": event.get("tool_call", "unknown"),
                    "arguments_keys": [],
                    "success": None,
                    "observation_chars": 0,
                    "duration_ms": 0,
                }
                entry["actions"].append(action)
            observation = str(event.get("observation") or "")
            action["success"] = bool(event.get("success", True))
            action["observation_chars"] = len(observation)
            action["duration_ms"] = int(event.get("duration_ms", 0) or 0)

        elif event_type == "recovery_decision":
            entry["recoveries"].append(
                {
                    "failure_kind": event.get("failure_kind", ""),
                    "retryable": bool(event.get("retryable", False)),
                    "recovery_hint": event.get("recovery_hint", ""),
                }
            )

        elif event_type == "hook_check":
            hook_result = event.get("hook_result") or {}
            entry["hook_checks"].append(
                {
                    "tool": event.get("tool_call", ""),
                    "decision": hook_result.get("decision", ""),
                    "reason": hook_result.get("reason", ""),
                    "hooks": [item.get("hook_name", "") for item in hook_result.get("decisions", [])],
                }
            )

        elif event_type == "task_state_checkpoint":
            task_state = event.get("task_state") or {}
            entry["task_states"].append(
                {
                    "status": task_state.get("status", ""),
                    "step": task_state.get("current_step", 0),
                    "last_tool": task_state.get("last_tool", ""),
                    "stop_reason": task_state.get("stop_reason", ""),
                }
            )

        elif event_type == "execution_environment":
            entry["runtime"]["execution_environment"] = event.get("execution_environment") or {}

        elif event_type == "permission_check":
            decision = str(event.get("permission_decision") or "")
            if decision in entry["permissions"]:
                entry["permissions"][decision] += 1

        elif event_type == "guardrail_check":
            guardrail = event.get("guardrail") or {}
            if not guardrail.get("passed", True):
                entry["guardrail_blocks"] += 1

        elif event_type == "error":
            entry["errors"].append(str(event.get("error") or ""))

        elif event_type == "evidence_collected":
            evidence = str(event.get("evidence") or "")
            if evidence:
                evidence_refs.append(evidence)

        elif event_type == "final_answer":
            for evidence in event.get("evidence_refs") or []:
                evidence_refs.append(str(evidence))

    ordered_steps = [steps[key] for key in sorted(steps)]
    tool_efficiency = _tool_efficiency(ordered_steps)
    context_breakdown = _context_breakdown(ordered_steps)
    summary = _summary(trace, ordered_steps, tool_efficiency, context_breakdown)
    return {
        "run_id": trace.get("run_id", ""),
        "task": trace.get("task", ""),
        "stop_reason": trace.get("stop_reason", ""),
        "final_answer": trace.get("final_answer", ""),
        "summary": summary,
        "steps": ordered_steps,
        "context_breakdown": context_breakdown,
        "tool_efficiency": tool_efficiency,
        "evidence_refs": _dedupe_keep_order(evidence_refs),
        "runtime_control": _runtime_control(ordered_steps),
        "optimization_notes": _optimization_notes(trace, summary, ordered_steps, context_breakdown, tool_efficiency),
    }


def render_usage_markdown(usage: dict[str, Any]) -> str:
    """Render the usage report as an engineering-readable markdown artifact."""

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
        f"- tokens: input={summary['prompt_tokens']} output={summary['completion_tokens']} total={summary['total_tokens']}",
        f"- cache: hit={summary['cache_hit_tokens']} miss={summary['cache_miss_tokens']} hit_rate={summary['cache_hit_rate']:.2%}",
        f"- estimated_cost_usd: ${summary['estimated_cost_usd']:.6f}",
        f"- llm_latency_ms: {summary['llm_latency_ms']}",
        f"- tool_calls: {summary['tool_calls']} failed={summary['failed_tool_calls']}",
        f"- hook_checks: {summary['hook_checks']}",
        f"- latest_task_status: `{summary['latest_task_status']}`",
        "",
        "## Runtime Control",
        "",
        f"- execution_environment: `{usage['runtime_control'].get('execution_environment_mode', '')}`",
        f"- active_workspace: `{usage['runtime_control'].get('active_workspace', '')}`",
        f"- network_policy: `{usage['runtime_control'].get('network_policy', '')}`",
        f"- hook_decisions: {usage['runtime_control'].get('hook_decisions', {})}",
        f"- task_statuses: {usage['runtime_control'].get('task_statuses', {})}",
        "",
        "## Step Breakdown",
        "",
        "| call | step | agent | provider/model | input | output | cache hit | cache miss | cost | latency ms | context chars | action summary |",
        "|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for step in usage["steps"]:
        context_chars = int((step.get("context") or {}).get("total_chars", 0) or 0)
        actions = _action_summary_text(step.get("actions", []))
        for call in step.get("llm_calls", []):
            model = f"{call.get('provider', '')}/{call.get('model', '')}".strip("/")
            lines.append(
                "|{call_index}|{step}|{agent}|{model}|{prompt}|{completion}|{hit}|{miss}|${cost:.6f}|{latency}|{context_chars}|{actions}|".format(
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
    lines.extend(_render_counter_table(usage["context_breakdown"]["section_chars"], "section", "chars"))
    lines.extend(["", "## Tool Efficiency", ""])
    lines.append("| tool | calls | success | failed | success rate | observation chars | duration ms |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for tool, data in usage["tool_efficiency"]["by_tool"].items():
        success_rate = data["success"] / data["calls"] if data["calls"] else 0.0
        lines.append(
            f"| {tool} | {data['calls']} | {data['success']} | {data['failed']} | {success_rate:.2%} | {data['observation_chars']} | {data['duration_ms']} |"
        )
    if not usage["tool_efficiency"]["by_tool"]:
        lines.append("| none | 0 | 0 | 0 | 0.00% | 0 | 0 |")

    lines.extend(["", "## Evidence", ""])
    evidence_refs = usage.get("evidence_refs") or []
    if evidence_refs:
        for evidence in evidence_refs[:12]:
            lines.append(f"- `{evidence}`")
    else:
        lines.append("- none")

    lines.extend(["", "## Optimization Notes", ""])
    for note in usage.get("optimization_notes", []):
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def _llm_call_summary(event: dict[str, Any], call_index: int, run_id: str) -> dict[str, Any]:
    usage = event.get("model_usage") or {}
    prompt_tokens = _int(usage.get("prompt_tokens")) or _int(usage.get("prompt_tokens_estimate"))
    completion_tokens = _int(usage.get("completion_tokens")) or _int(usage.get("completion_tokens_estimate"))
    total_tokens = _int(usage.get("total_tokens")) or prompt_tokens + completion_tokens
    return {
        "call_index": call_index,
        "call_id": f"{run_id}:llm:{call_index}",
        "provider_response_id": usage.get("response_id", ""),
        "provider": usage.get("provider", ""),
        "model": usage.get("model", ""),
        "usage_source": usage.get("usage_source", "estimate"),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cache_hit_tokens": _int(usage.get("cache_hit_tokens")),
        "cache_miss_tokens": _int(usage.get("cache_miss_tokens")),
        "reasoning_tokens": _int(usage.get("reasoning_tokens")),
        "prompt_tokens_estimate": _int(usage.get("prompt_tokens_estimate")),
        "completion_tokens_estimate": _int(usage.get("completion_tokens_estimate")),
        "estimated_cost_usd": float(usage.get("estimated_cost_usd") or 0.0),
        "latency_ms": _int(usage.get("latency_ms")),
        "attempts": _int(usage.get("attempts")),
        "fallback_used": bool(usage.get("fallback_used", False)),
        "error_codes": list(usage.get("error_codes") or []),
        "request_summary": event.get("llm_request_summary", ""),
        "response_summary": event.get("llm_response_summary", ""),
        "input_breakdown_chars": event.get("llm_input_breakdown_chars") or {},
    }


def _context_summary(context: dict[str, Any]) -> dict[str, Any]:
    breakdown = {str(k): _int(v) for k, v in (context.get("budget_breakdown") or {}).items()}
    return {
        "total_chars": _int(context.get("total_chars")),
        "max_chars": _int(context.get("max_chars")),
        "truncated": bool(context.get("truncated", False)),
        "budget_breakdown_chars": breakdown,
        "budget_breakdown_estimated_tokens": {key: _chars_to_tokens(value) for key, value in breakdown.items()},
        "selected_files_count": len(context.get("selected_files") or []),
        "retrieved_docs_count": _int(context.get("retrieved_docs_count")),
        "dropped_context_count": len(context.get("dropped_context") or []),
        "topic_relation": context.get("topic_relation", ""),
        "inherit_session": bool(context.get("inherit_session", False)),
    }


def _tool_efficiency(steps: list[dict[str, Any]]) -> dict[str, Any]:
    by_tool: dict[str, dict[str, int]] = {}
    for step in steps:
        for action in step.get("actions", []):
            tool = action.get("tool") or "unknown"
            stats = by_tool.setdefault(
                tool,
                {"calls": 0, "success": 0, "failed": 0, "observation_chars": 0, "duration_ms": 0},
            )
            stats["calls"] += 1
            if action.get("success") is False:
                stats["failed"] += 1
            else:
                stats["success"] += 1
            stats["observation_chars"] += _int(action.get("observation_chars"))
            stats["duration_ms"] += _int(action.get("duration_ms"))
    return {
        "by_tool": dict(sorted(by_tool.items())),
        "total_calls": sum(item["calls"] for item in by_tool.values()),
        "failed_calls": sum(item["failed"] for item in by_tool.values()),
    }


def _context_breakdown(steps: list[dict[str, Any]]) -> dict[str, Any]:
    section_chars: Counter[str] = Counter()
    input_chars: Counter[str] = Counter()
    truncated_steps = 0
    for step in steps:
        context = step.get("context") or {}
        if context.get("truncated"):
            truncated_steps += 1
        section_chars.update(context.get("budget_breakdown_chars") or {})
        for call in step.get("llm_calls", []):
            input_chars.update(call.get("input_breakdown_chars") or {})
    merged = Counter(section_chars)
    merged.update(input_chars)
    return {
        "section_chars": dict(sorted(merged.items())),
        "section_estimated_tokens": {key: _chars_to_tokens(value) for key, value in sorted(merged.items())},
        "truncated_steps": truncated_steps,
    }


def _summary(
    trace: dict[str, Any],
    steps: list[dict[str, Any]],
    tool_efficiency: dict[str, Any],
    context_breakdown: dict[str, Any],
) -> dict[str, Any]:
    calls = [call for step in steps for call in step.get("llm_calls", [])]
    prompt_tokens = sum(call["prompt_tokens"] for call in calls)
    completion_tokens = sum(call["completion_tokens"] for call in calls)
    total_tokens = sum(call["total_tokens"] for call in calls) or prompt_tokens + completion_tokens
    cache_hit = sum(call["cache_hit_tokens"] for call in calls)
    cache_miss = sum(call["cache_miss_tokens"] for call in calls)
    cache_total = cache_hit + cache_miss
    hook_checks = [check for step in steps for check in step.get("hook_checks", [])]
    task_states = [state for step in steps for state in step.get("task_states", [])]
    return {
        "llm_calls": len(calls),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cache_hit_tokens": cache_hit,
        "cache_miss_tokens": cache_miss,
        "cache_hit_rate": cache_hit / cache_total if cache_total else 0.0,
        "reasoning_tokens": sum(call["reasoning_tokens"] for call in calls),
        "estimated_cost_usd": round(sum(call["estimated_cost_usd"] for call in calls), 6),
        "llm_latency_ms": sum(call["latency_ms"] for call in calls),
        "steps": len(steps),
        "tool_calls": tool_efficiency["total_calls"],
        "failed_tool_calls": tool_efficiency["failed_calls"],
        "hook_checks": len(hook_checks),
        "latest_task_status": task_states[-1]["status"] if task_states else "",
        "truncated_context_steps": context_breakdown["truncated_steps"],
        "trace_event_count": len(trace.get("events", [])),
    }


def _runtime_control(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate environment, hook, and task-state control-plane signals."""

    hook_decisions: Counter[str] = Counter()
    task_statuses: Counter[str] = Counter()
    environment = {}
    for step in steps:
        if not environment and step.get("runtime", {}).get("execution_environment"):
            environment = step["runtime"]["execution_environment"]
        for check in step.get("hook_checks", []):
            hook_decisions.update([check.get("decision") or "unknown"])
        for state in step.get("task_states", []):
            task_statuses.update([state.get("status") or "unknown"])
    return {
        "execution_environment_mode": environment.get("mode", ""),
        "active_workspace": environment.get("active_workspace", ""),
        "network_policy": environment.get("network_policy", ""),
        "hook_decisions": dict(sorted(hook_decisions.items())),
        "task_statuses": dict(sorted(task_statuses.items())),
    }


def _optimization_notes(
    trace: dict[str, Any],
    summary: dict[str, Any],
    steps: list[dict[str, Any]],
    context_breakdown: dict[str, Any],
    tool_efficiency: dict[str, Any],
) -> list[str]:
    notes = []
    if summary["llm_calls"] == 0:
        notes.append("No LLM calls were made; this run is a deterministic workflow or early guardrail stop.")
    if summary["cache_miss_tokens"] and summary["cache_hit_rate"] < 0.2:
        notes.append("Cache hit rate is low; stable system/context prefixes may not be reused enough across steps.")
    if summary["truncated_context_steps"]:
        notes.append(f"Context was truncated in {summary['truncated_context_steps']} step(s); inspect dropped_context and selected files.")
    if tool_efficiency["failed_calls"]:
        notes.append(f"{tool_efficiency['failed_calls']} tool observation(s) failed; connect these to recovery_decision events.")
    if trace.get("stop_reason") in {"max_steps", "max_steps reached"}:
        notes.append("Run stopped at max_steps; tune prompt, tool routing, or max_steps depending on whether progress was real.")

    section_chars = context_breakdown.get("section_chars") or {}
    if section_chars:
        top_section, top_chars = max(section_chars.items(), key=lambda item: item[1])
        notes.append(f"Largest context section is {top_section} ({top_chars} chars); this is the first place to optimize token cost.")
    if not notes:
        notes.append("No obvious usage hotspot detected; compare this run against another model or prompt variant.")
    return notes


def _last_action_without_observation(step: dict[str, Any]) -> dict[str, Any] | None:
    for action in reversed(step.get("actions", [])):
        if action.get("success") is None:
            return action
    return None


def _action_summary_text(actions: list[dict[str, Any]]) -> str:
    if not actions:
        return ""
    counts = Counter(action.get("tool") or "unknown" for action in actions)
    return ", ".join(f"{tool}x{count}" for tool, count in sorted(counts.items()))


def _render_counter_table(counter: dict[str, int], key_label: str, value_label: str) -> list[str]:
    lines = [f"| {key_label} | {value_label} | est tokens |", "|---|---:|---:|"]
    for key, value in sorted(counter.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {key} | {value} | {_chars_to_tokens(value)} |")
    if not counter:
        lines.append("| none | 0 | 0 |")
    return lines


def _dedupe_keep_order(items: list[str]) -> list[str]:
    """Deduplicate evidence strings without hiding their original order."""

    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _chars_to_tokens(chars: int) -> int:
    # Rough local estimate for context sections. Provider token usage remains
    # authoritative when returned by the model API.
    return max(0, int(round(_int(chars) / 4)))


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

from __future__ import annotations

from typing import Any


def render_trace_summary(trace: dict[str, Any]) -> str:

    metrics = trace.get("metrics", {})
    lines = [
        "# NanoHarness Run Summary",
        "",
        f"- run_id: {trace.get('run_id')}",
        f"- task: {trace.get('task', '')}",
        f"- stop_reason: {trace.get('stop_reason', '')}",
        f"- final_answer: {str(trace.get('final_answer', ''))[:200]}",
        "",
        "## Metrics",
    ]
    if isinstance(metrics, dict):
        for key, value in metrics.items():
            lines.append(f"- {key}: {value}")
    lines.extend(["", "## Events"])
    events = trace.get("events")
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        lines.append(
            f"- step={event.get('step')} agent={event.get('agent_name')} "
            f"type={event.get('event_type')} success={event.get('success')}"
        )
    return "\n".join(lines)

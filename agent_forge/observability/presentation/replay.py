"""把事实 trace 渲染为便于终端检查的紧凑时间线。"""

from __future__ import annotations

from typing import Any


def render_trace_replay(trace: dict[str, Any]) -> str:
    """渲染一份 trace read model，不修改原始证据。"""

    lines = [
        f"run_id: {trace.get('run_id', '')}",
        f"task: {trace.get('task', '')}",
        f"stop_reason: {trace.get('stop_reason', '')}",
        "",
        "|step|agent|event|success|summary|",
        "|---:|---|---|---|---|",
    ]
    for event in trace.get("events", []):
        summary = (
            event.get("tool_call")
            or event.get("failure_kind")
            or event.get("permission_decision")
            or event.get("error")
            or ""
        )
        if not summary and event.get("event_type") == "context_assembly":
            context = event.get("context") or {}
            summary = (
                f"files={len(context.get('selected_files') or [])} "
                f"tools={len(context.get('available_tools') or [])}"
            )
        lines.append(
            f"|{event.get('step', 0)}|{event.get('agent_name', '')}|"
            f"{event.get('event_type', '')}|{event.get('success', True)}|"
            f"{str(summary)[:120]}|"
        )
    return "\n".join(lines) + "\n"

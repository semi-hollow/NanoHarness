from pathlib import Path


def write_summary(path: str | Path, trace: dict) -> None:
    trace_path = Path(path)
    summary_path = trace_path.with_name("summary.md")
    metrics = trace.get("metrics", {})
    lines = [
        "# Agent Forge Run Summary",
        "",
        f"- run_id: {trace.get('run_id')}",
        f"- task: {trace.get('task', '')}",
        f"- stop_reason: {trace.get('stop_reason', '')}",
        f"- final_answer: {trace.get('final_answer', '')[:200]}",
        "",
        "## Metrics",
    ]
    for key, value in metrics.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Events"])
    for event in trace.get("events", []):
        lines.append(
            f"- step={event.get('step')} agent={event.get('agent_name')} "
            f"type={event.get('event_type')} success={event.get('success')}"
        )
    summary_path.write_text("\n".join(lines), encoding="utf-8")

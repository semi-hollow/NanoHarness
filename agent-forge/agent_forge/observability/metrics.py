import json
from pathlib import Path


def summarize(events: list[dict]) -> dict:
    """Aggregate trace events into the counters shown in reports."""

    numeric_steps = [int(event.get("step", 0) or 0) for event in events]
    return {
        "tool_call_count": sum(event.get("event_type") == "tool_call" for event in events),
        "failed_tool_call_count": sum(
            event.get("event_type") == "tool_observation" and not event.get("success", True)
            for event in events
        ),
        "handoff_count": sum(event.get("event_type") == "handoff" for event in events),
        "guardrail_block_count": sum(
            event.get("event_type") == "guardrail_check"
            and not event.get("guardrail", {}).get("passed", True)
            for event in events
        ),
        "approval_count": sum(event.get("event_type") == "human_approval" for event in events),
        "permission_denied_count": sum(
            event.get("event_type") == "permission_check"
            and event.get("permission_decision") == "deny"
            for event in events
        ),
        "error_count": sum(event.get("event_type") == "error" for event in events),
        "test_command_count": sum(
            event.get("event_type") == "tool_call" and event.get("tool_call") == "run_command"
            for event in events
        ),
        "duration_ms": sum(int(event.get("duration_ms", 0) or 0) for event in events),
        "agent_steps_count": max(numeric_steps) if numeric_steps else 0,
        "trace_event_count": len(events),
        "steps_count": max(numeric_steps) if numeric_steps else 0,
    }


def summarize_trace(trace: dict) -> dict:
    """Summarize a full trace dict by reading its `events` list."""

    return summarize(trace.get("events", []))


def summarize_trace_file(path: str | Path) -> dict:
    """Load one trace JSON file and summarize it."""

    return summarize_trace(json.loads(Path(path).read_text(encoding="utf-8")))

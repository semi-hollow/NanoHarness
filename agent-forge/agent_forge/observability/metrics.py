import json
from pathlib import Path


def summarize(events: list[dict]) -> dict:
    return {
        "tool_call_count": sum(e.get("event_type") == "tool_call" for e in events),
        "failed_tool_call_count": sum(e.get("event_type") == "tool_observation" and not e.get("success", True) for e in events),
        "handoff_count": sum(e.get("event_type") == "handoff" for e in events),
        "guardrail_block_count": sum(e.get("event_type") == "guardrail_check" and not e.get("guardrail", {}).get("passed", True) for e in events),
        "approval_count": sum(e.get("event_type") == "human_approval" for e in events),
        "permission_denied_count": sum(e.get("event_type") == "permission_check" and e.get("permission_decision") == "deny" for e in events),
        "error_count": sum(e.get("event_type") == "error" for e in events),
        "test_command_count": sum(e.get("event_type") == "tool_call" and e.get("tool_call") == "run_command" for e in events),
        "duration_ms": sum(int(e.get("duration_ms", 0) or 0) for e in events),
        "steps_count": len(events),
    }


def summarize_trace(trace: dict) -> dict:
    return summarize(trace.get("events", []))


def summarize_trace_file(path: str | Path) -> dict:
    return summarize_trace(json.loads(Path(path).read_text(encoding="utf-8")))

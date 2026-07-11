from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LocalValidation:
    """Conservative local test evidence derived from explicit trace events."""

    status: str = "not_run"
    evidence: list[str] = field(default_factory=list)


def read_local_validation(trace_path: str | Path) -> LocalValidation:
    """Return failed/unavailable/passed only for test-oriented trace events."""

    try:
        trace = json.loads(Path(trace_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return LocalValidation()

    records = []
    for event in trace.get("events", []):
        if not isinstance(event, dict) or event.get("event_type") != "validation_evidence":
            continue
        validation = event.get("validation")
        if isinstance(validation, dict):
            records.append(validation)
    if not records:
        return LocalValidation()

    statuses = {str(record.get("status") or "") for record in records}
    if "failed" in statuses:
        status = "failed"
    elif "unavailable" in statuses:
        status = "unavailable"
    elif statuses == {"passed"}:
        status = "passed"
    else:
        status = "failed"
    evidence = [
        f"{record.get('kind', 'test')}:{record.get('status', 'unknown')}:{str(record.get('evidence') or '')[:300]}"
        for record in records
    ]
    return LocalValidation(status=status, evidence=evidence)

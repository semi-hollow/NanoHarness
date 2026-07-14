import time
from dataclasses import dataclass


@dataclass
class MemoryRecord:

    key: str
    value: str
    scope: str = "session"
    confidence: float = 1.0
    ttl_seconds: float | None = None
    source: str = "runtime"
    agent_name: str = "agent"
    created_at: float = 0.0

    def __post_init__(self) -> None:

        if not self.created_at:
            self.created_at = time.time()

    def expired(self, now: float | None = None) -> bool:

        if self.ttl_seconds is None:
            return False
        return (now or time.time()) - self.created_at > self.ttl_seconds


class MemoryPolicy:

    def __init__(self, min_confidence: float = 0.55) -> None:

        self.min_confidence = min_confidence

    def visible_records(self, records: list[MemoryRecord], *, agent_name: str = "agent") -> list[MemoryRecord]:

        visible = []
        for record in records:
            if record.expired():
                continue
            if record.confidence < self.min_confidence:
                continue
            if record.scope == "agent_private" and record.agent_name != agent_name:
                continue
            visible.append(record)
        return visible

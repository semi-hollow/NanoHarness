class EventStore:
    """In-memory event table used to make idempotency visible in tests."""

    def __init__(self):
        self.events = []

    def insert_event(self, event_id: str, event_type: str, payload: dict) -> None:
        self.events.append(
            {
                "event_id": event_id,
                "event_type": event_type,
                "payload": dict(payload),
            }
        )

    def exists(self, event_id: str) -> bool:
        return any(item["event_id"] == event_id for item in self.events)

    def count(self, event_id: str) -> int:
        return sum(1 for item in self.events if item["event_id"] == event_id)

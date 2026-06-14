class JobQueue:
    """In-memory queue used to prove duplicate side effects are blocked."""

    def __init__(self):
        self.jobs = []

    def enqueue(self, event_id: str, event_type: str) -> None:
        self.jobs.append(
            {
                "event_id": event_id,
                "event_type": event_type,
            }
        )

    def count(self, event_id: str) -> int:
        return sum(1 for item in self.jobs if item["event_id"] == event_id)

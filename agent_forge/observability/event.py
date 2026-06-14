from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class TraceEvent:
    """Typed event shape retained for tests and future trace refactors."""

    run_id: str
    step: int
    agent_name: str
    event_type: str
    success: bool = True
    error: str = ""
    data: dict[str, Any] | None = None

    def to_dict(self):
        """Convert to a plain dict and normalize missing extra data."""

        data = asdict(self)
        if data["data"] is None:
            data["data"] = {}
        return data

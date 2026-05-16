from dataclasses import dataclass


@dataclass
class Handoff:
    """Traceable payload for one supervisor-to-subagent transition."""

    from_agent: str
    to_agent: str
    reason: str
    payload: dict

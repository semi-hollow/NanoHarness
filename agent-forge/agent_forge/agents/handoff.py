from dataclasses import dataclass


@dataclass
class Handoff:
    """Traceable payload for one supervisor-to-subagent transition.

    The handoff object exists because multi-agent systems are hard to debug if
    "who gave what context to whom" is only implicit in logs. This demo keeps
    the payload small, but the production idea is the same: every transfer of
    responsibility should be auditable.
    """

    # Sender role.
    from_agent: str

    # Receiver role.
    to_agent: str

    # Why this handoff happened, usually a task/node id.
    reason: str

    # Structured state excerpt passed across the boundary.
    payload: dict

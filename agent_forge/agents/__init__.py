"""Multi-agent orchestration package.

Why this package exists:
    Multi-agent here means role separation on top of the same governed runtime,
    not uncontrolled peer-to-peer chatting. The supervisor owns phase policy,
    role specs, handoff evidence, and retry boundaries.

Read first:
    ``supervisor_agent.py`` shows the orchestration path.
    ``supervisor_policy.py`` decides next phase from state.
    ``handoff.py`` records agent-to-agent transfer metadata.

If removed:
    The project could still run a single CodingAgent, but it could not explain
    supervisor-style decomposition, reviewer gates, or controlled role handoff.
"""

from .supervisor_agent import SupervisorAgent

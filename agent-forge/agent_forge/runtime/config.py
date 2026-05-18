from dataclasses import dataclass


@dataclass
class RuntimeConfig:
    """Runtime knobs passed from CLI into AgentLoop.

    These settings are the control plane for a coding agent. They make the
    behavior interview-readable: context budget, loop budget, failure budget,
    timeout, and optional resumed-session context are explicit runtime inputs.
    """

    workspace: str
    max_steps: int = 12
    auto_approve_writes: bool = True
    trace_file: str = "agent_forge_trace.json"
    max_context_chars: int = 8000
    max_consecutive_failures: int = 3
    max_tool_repeats: int = 2
    timeout_seconds: float = 120.0
    cost_budget_usd: float | None = None
    previous_task: str = ""
    session_summary: str = ""

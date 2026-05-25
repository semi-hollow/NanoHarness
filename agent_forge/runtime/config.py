from dataclasses import dataclass


@dataclass
class RuntimeConfig:
    """Runtime knobs passed from CLI into AgentLoop.

    These settings are the control plane for a coding agent. They make the
    behavior technical-review-readable: context budget, loop budget, failure budget,
    timeout, and optional resumed-session context are explicit runtime inputs.
    """

    # Workspace root. All tools must stay inside this sandbox boundary.
    workspace: str

    # ReAct iteration cap. Prevents the agent from running forever.
    max_steps: int = 12

    # Demo convenience flag. False forces write-like actions through approval denial.
    auto_approve_writes: bool = True

    # JSON trace destination for audit/replay.
    trace_file: str = "agent_forge_trace.json"

    # Prompt-context cap. ContextStrategy decides how to spend this budget.
    max_context_chars: int = 8000

    # Stop after this many failed tool observations in a row.
    max_consecutive_failures: int = 3

    # Stop repeated identical tool calls, a common ReAct loop failure.
    max_tool_repeats: int = 2

    # Wall-clock run limit, separate from provider request timeout.
    timeout_seconds: float = 120.0

    # Optional model-spend budget hook.
    cost_budget_usd: float | None = None

    # Previous run task used by ContextStrategy to judge topic continuity.
    previous_task: str = ""

    # Compressed previous run report used only when topic inheritance is safe.
    session_summary: str = ""

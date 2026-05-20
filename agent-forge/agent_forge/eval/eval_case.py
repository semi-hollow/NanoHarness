from dataclasses import dataclass, field


@dataclass
class EvalResult:
    """Structured result for one executable eval case."""

    # Folder name, stable eval id.
    case_id: str

    # Overall pass flag after combining task/test/safety.
    passed: bool

    # Did the agent solve the requested task?
    task_success: bool

    # Did validation pass?
    test_pass: bool

    # Did the case detect unsafe behavior?
    safety_violation: bool

    # Operational metrics copied from trace.
    handoff_count: int
    tool_call_count: int
    agent_steps_count: int
    trace_event_count: int
    permission_denied_count: int
    guardrail_block_count: int
    failed_tool_call_count: int

    # Short human-readable note from the verify script.
    notes: str

    # Full metrics dict for deeper reports.
    metrics: dict = field(default_factory=dict)

    # Original task text and verify command for reproducibility.
    task: str = ""
    command: str = ""

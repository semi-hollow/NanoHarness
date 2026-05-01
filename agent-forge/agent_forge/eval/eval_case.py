from dataclasses import dataclass, field


@dataclass
class EvalResult:
    case_id: str
    passed: bool
    task_success: bool
    test_pass: bool
    safety_violation: bool
    handoff_count: int
    tool_call_count: int
    agent_steps_count: int
    trace_event_count: int
    permission_denied_count: int
    guardrail_block_count: int
    failed_tool_call_count: int
    notes: str
    metrics: dict = field(default_factory=dict)
    task: str = ""
    command: str = ""

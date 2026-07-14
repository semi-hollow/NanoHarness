from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuntimeConfig:

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
    execution_environment: Any | None = None
    task_state_root: str = ".agent_forge/task_state"
    resume_state: str = ""
    approval_root: str = ".agent_forge/approvals"
    human_input_root: str = ".agent_forge/human_input"
    human_thread_id: str = ""
    operation_ledger_root: str = ".agent_forge/operation_ledger"
    approval_mode: str = "trusted"
    skill_mode: str = "auto"
    skill_names: list[str] = field(default_factory=list)
    skill_manifest_files: list[str] = field(default_factory=list)
    tool_routing_mode: str = "task-aware"

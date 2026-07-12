from dataclasses import dataclass, field
from typing import Any


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

    # Local convenience flag. False forces write-like actions through approval denial.
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

    # Prepared execution environment. Local runs use path/command checks;
    # worktree runs execute in an isolated git worktree.
    execution_environment: Any | None = None

    # Filesystem root for resumable task checkpoints.
    task_state_root: str = ".agent_forge/task_state"

    # Optional prior task-state id used to seed continuation context.
    resume_state: str = ""

    # Filesystem root for pending human approval requests.
    approval_root: str = ".agent_forge/approvals"

    # Filesystem queue and stable conversation identity for durable questions.
    human_input_root: str = ".agent_forge/human_input"
    human_thread_id: str = ""

    # Filesystem root for idempotency records of side-effectful operations.
    operation_ledger_root: str = ".agent_forge/operation_ledger"

    # trusted/on-write/on-risk/locked/dry-run approval posture.
    approval_mode: str = "trusted"

    # auto selects concrete coding skills from the task; none disables them.
    skill_mode: str = "auto"

    # Explicit skill names override automatic selection. Keep empty for auto.
    skill_names: list[str] = field(default_factory=list)

    # Optional custom Skill manifests loaded after built-in coding skills.
    skill_manifest_files: list[str] = field(default_factory=list)

    # task-aware narrows model-visible schemas; all is the controlled ablation
    # while permission, command, approval, and sandbox policies stay enabled.
    tool_routing_mode: str = "task-aware"

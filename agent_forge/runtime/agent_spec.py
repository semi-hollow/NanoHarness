from dataclasses import dataclass, field

from agent_forge.workflows.artifact import TaskArtifact


@dataclass
class AgentSpec:
    """Role contract for one runtime-backed agent.

    This is the bridge from legacy role functions to production-style workers.
    A supervisor can schedule several specs with different prompts, tool
    allowlists, and step budgets while reusing the same AgentLoop runtime.
    """

    # Stable worker name shown in trace/handoff.
    name: str

    # Semantic role used by reports and artifacts.
    role: str

    # Role instruction injected into the user task before AgentLoop runs.
    system_prompt: str = ""

    # Tool allowlist. Empty means "all shared registry tools"; non-empty means
    # this role sees only those tool schemas.
    allowed_tools: set[str] = field(default_factory=set)

    # Per-role loop budget. Test/review roles usually need fewer steps.
    max_steps: int = 6

    # Files this role may write; used by OwnershipPlan/TaskScheduler.
    write_files: set[str] = field(default_factory=set)

    # Files this role is expected to inspect; used for artifacts and context.
    read_files: set[str] = field(default_factory=set)

    # Human risk label for reports. It can drive stricter approval in production.
    risk_level: str = "low"

    def task_for_role(self, task: str) -> str:
        """Inject role context without hiding it in custom supervisor code."""

        if not self.system_prompt:
            return task
        return f"{self.system_prompt}\n\nTask: {task}"


@dataclass
class AgentRunResult:
    """Structured result returned by AgentRuntime.

    Production systems should not pass only strings between agents. This result
    keeps the human-facing answer plus machine-readable counters that a
    supervisor, report writer, or eval runner can inspect.
    """

    # Which role produced this result.
    agent_name: str

    # Human-facing result from the subagent.
    final_answer: str

    # Machine success flag used by TaskScheduler.
    success: bool

    # Trace slice produced during this subagent run.
    events: list[dict]

    # Tool counters give supervisor/eval cheap operational evidence.
    tool_call_count: int = 0
    failed_tool_call_count: int = 0

    # Typed outputs handed to downstream nodes.
    artifacts: list[TaskArtifact] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return JSON-safe result data for session artifacts."""

        return {
            "agent_name": self.agent_name,
            "final_answer": self.final_answer,
            "success": self.success,
            "tool_call_count": self.tool_call_count,
            "failed_tool_call_count": self.failed_tool_call_count,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }

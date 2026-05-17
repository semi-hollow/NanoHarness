from dataclasses import dataclass, field

from agent_forge.workflows.artifact import TaskArtifact


@dataclass
class AgentSpec:
    """Role contract for one runtime-backed agent.

    This is the bridge from legacy role functions to production-style workers.
    A supervisor can schedule several specs with different prompts, tool
    allowlists, and step budgets while reusing the same AgentLoop runtime.
    """

    name: str
    role: str
    system_prompt: str = ""
    allowed_tools: set[str] = field(default_factory=set)
    max_steps: int = 6
    write_files: set[str] = field(default_factory=set)
    read_files: set[str] = field(default_factory=set)
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

    agent_name: str
    final_answer: str
    success: bool
    events: list[dict]
    tool_call_count: int = 0
    failed_tool_call_count: int = 0
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

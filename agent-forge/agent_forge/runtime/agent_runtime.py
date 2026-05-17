from agent_forge.observability.metrics import summarize
from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.observation import Observation

from .agent_spec import AgentRunResult, AgentSpec


class FilteredToolRegistry:
    """Per-agent tool allowlist over the shared ToolRegistry.

    This is a production concern: planner, coder, tester, and reviewer should
    not automatically get the same tool permissions. The wrapper keeps the
    original registry intact while exposing a restricted view to AgentLoop.
    """

    def __init__(self, registry, allowed_tools: set[str]):
        """Keep the registry plus the allowlist supplied by AgentSpec."""

        self.registry = registry
        self.allowed_tools = allowed_tools

    def schemas(self) -> list[dict]:
        """Expose only tools this role is allowed to see."""

        if not self.allowed_tools:
            return self.registry.schemas()
        return [schema for schema in self.registry.schemas() if schema.get("name") in self.allowed_tools]

    def get(self, name: str):
        """Return None for tools outside the role allowlist."""

        if self.allowed_tools and name not in self.allowed_tools:
            return None
        return self.registry.get(name)

    def execute(self, name: str, arguments: dict) -> Observation:
        """Deny out-of-role tools as observations, not uncaught exceptions."""

        if self.allowed_tools and name not in self.allowed_tools:
            return Observation(name, False, f"tool not allowed for this agent: {name}")
        return self.registry.execute(name, arguments)


class AgentRuntime:
    """Reusable runtime wrapper around AgentLoop for role-specific workers.

    This is the key upgrade from the original MVP: multi-agent no longer has to
    be a set of hard-coded role functions. A supervisor can schedule workers
    that all share the same loop semantics: context assembly, LLM calls, tool
    policy, observations, stop conditions, and trace.
    """

    def __init__(self, spec: AgentSpec, workspace: str, registry, trace, llm, auto_approve_writes: bool = True):
        """Inject role spec and runtime dependencies from the supervisor/CLI."""

        self.spec = spec
        self.workspace = workspace
        self.registry = FilteredToolRegistry(registry, spec.allowed_tools)
        self.trace = trace
        self.llm = llm
        self.auto_approve_writes = auto_approve_writes

    def run(self, task: str) -> AgentRunResult:
        """Run one role through AgentLoop and return structured evidence."""

        start_index = len(self.trace.events)
        config = RuntimeConfig(
            workspace=self.workspace,
            max_steps=self.spec.max_steps,
            auto_approve_writes=self.auto_approve_writes,
            trace_file=self.trace.path,
        )
        loop = AgentLoop(config, self.trace, self.registry, self.llm)
        final_answer = loop.run(self.spec.task_for_role(task), agent_name=self.spec.name)
        events = self.trace.events[start_index:]
        metrics = summarize(events)
        success = not str(final_answer).startswith("blocked:")
        return AgentRunResult(
            agent_name=self.spec.name,
            final_answer=final_answer,
            success=success,
            events=events,
            tool_call_count=int(metrics.get("tool_call_count", 0)),
            failed_tool_call_count=int(metrics.get("failed_tool_call_count", 0)),
        )

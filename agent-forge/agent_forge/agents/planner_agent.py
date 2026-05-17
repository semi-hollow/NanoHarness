from .base_agent import BaseAgent, AgentResult


class PlannerAgent(BaseAgent):
    """Create the high-level plan used by the supervised multi-agent demo.

    This is not an LLM planner. It writes a predictable plan into shared state
    so the rest of the orchestration path has something to hand off and trace.
    Production planning would produce structured tasks, dependencies, owners,
    and acceptance criteria.
    """

    name = "PlannerAgent"

    def run(self, state):
        """Store a simple plan in shared state for later agents to reference."""

        task = state.get("task", "")
        plan = "1) read files 2) patch bug 3) run tests 4) review"
        state["plan"] = plan
        return AgentResult(self.name, f"Task: {task}\nPlan: {plan}")

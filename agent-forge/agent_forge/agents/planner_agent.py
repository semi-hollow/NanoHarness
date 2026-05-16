from .base_agent import BaseAgent, AgentResult


class PlannerAgent(BaseAgent):
    """Create the high-level plan used by the supervised multi-agent demo."""

    name = "PlannerAgent"

    def run(self, state):
        """Store a simple plan in shared state for later agents to reference."""

        task = state.get("task", "")
        plan = "1) read files 2) patch bug 3) run tests 4) review"
        state["plan"] = plan
        return AgentResult(self.name, f"Task: {task}\nPlan: {plan}")

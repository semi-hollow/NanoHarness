from .base_agent import BaseAgent, AgentResult


class PlannerAgent(BaseAgent):
    name = "PlannerAgent"

    def run(self, state):
        task = state.get("task", "")
        plan = "1) read files 2) patch bug 3) run tests 4) review"
        state["plan"] = plan
        return AgentResult(self.name, f"Task: {task}\nPlan: {plan}")

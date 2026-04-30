from .base_agent import BaseAgent, AgentResult

class PlannerAgent(BaseAgent):
    name="PlannerAgent"
    def run(self, state):
        task=state.get("task","")
        plan=["read target files","patch bug","run tests","review diff"]
        return AgentResult(self.name,f"Task: {task}\nPlan: {plan}")

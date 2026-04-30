from .base_agent import BaseAgent, AgentResult

class ReviewerAgent(BaseAgent):
    name="ReviewerAgent"
    def run(self, state):
        return AgentResult(self.name,"Reviewed diff for scope/safety and provided go/no-go.")

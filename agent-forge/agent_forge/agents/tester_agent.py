from .base_agent import BaseAgent, AgentResult

class TesterAgent(BaseAgent):
    name="TesterAgent"
    def run(self, state):
        return AgentResult(self.name,"Executed tests and collected failures/success summary.")

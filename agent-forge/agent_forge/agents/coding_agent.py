from .base_agent import BaseAgent, AgentResult

class CodingAgent(BaseAgent):
    name="CodingAgent"
    def run(self, state):
        return AgentResult(self.name,"Implemented code change candidate and prepared patch.")

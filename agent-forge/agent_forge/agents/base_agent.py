from dataclasses import dataclass

@dataclass
class AgentResult:
    name:str
    output:str

class BaseAgent:
    name="BaseAgent"
    def run(self, state:dict)->AgentResult:
        raise NotImplementedError

from .base_agent import BaseAgent, AgentResult


class TesterAgent(BaseAgent):
    name = "TesterAgent"

    def run(self, state):
        obs = state["registry"].execute("run_command", {"command": "python3.11 -m unittest discover examples/demo_repo/tests -t examples/demo_repo"})
        state["test_result"] = obs.content
        state["test_pass"] = obs.success
        return AgentResult(self.name, obs.content)

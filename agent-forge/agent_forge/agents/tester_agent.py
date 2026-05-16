from .base_agent import BaseAgent, AgentResult


class TesterAgent(BaseAgent):
    """Run the demo repository tests and store pass/fail state."""

    name = "TesterAgent"

    def run(self, state):
        """Execute unittest through the registry so command policy still applies."""

        obs = state["registry"].execute(
            "run_command",
            {"command": "python3.11 -m unittest discover examples/demo_repo/tests -t examples/demo_repo"},
        )
        state["test_result"] = obs.content
        state["test_pass"] = obs.success
        return AgentResult(self.name, obs.content)

from .base_agent import BaseAgent, AgentResult


class TesterAgent(BaseAgent):
    """Run the demo repository tests and store pass/fail state.

    This role exists to make "independent validation" visible in the supervised
    path. In production it would own test selection, flaky-test handling,
    coverage signals, and failure triage. Here it deliberately runs one fixed
    unittest command so the demo stays deterministic.
    """

    name = "TesterAgent"

    def run(self, state):
        """Execute unittest through the registry so command policy still applies.

        Even this scripted subagent does not call subprocess directly. It goes
        through ToolRegistry so command policy, sandbox rules, and trace shape
        remain the same boundary used by AgentLoop.
        """

        obs = state["registry"].execute(
            "run_command",
            {"command": "python3.11 -m unittest discover examples/demo_repo/tests -t examples/demo_repo"},
        )
        state["test_result"] = obs.content
        state["test_pass"] = obs.success
        return AgentResult(self.name, obs.content)

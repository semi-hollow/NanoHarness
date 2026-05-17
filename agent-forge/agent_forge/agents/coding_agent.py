from .base_agent import BaseAgent, AgentResult


class CodingAgent(BaseAgent):
    """Perform the code edit step in the supervised multi-agent demo.

    This agent is intentionally scripted around ``examples/demo_repo``. It is a
    teaching stub for "a coding worker made a change", not a general coding
    agent. The real general-purpose coding behavior lives in AgentLoop single
    mode, where an LLM chooses tools based on observations.
    """

    name = "CodingAgent"

    def run(self, state):
        """Read the demo file and patch it; retry uses the corrected old text.

        The first attempt uses the wrong old text on purpose so tester failure
        can drive a retry. That makes the supervisor loop visible in trace.
        """

        registry = state["registry"]
        trace = state["trace"]
        step = state.setdefault("step", 10)
        registry.execute("read_file", {"path": "examples/demo_repo/src/calculator.py"})
        old_text = "return a * b" if state.get("retry_count", 0) == 0 else "return a - b"
        patch = registry.execute(
            "apply_patch",
            {
                "path": "examples/demo_repo/src/calculator.py",
                "old": old_text,
                "new": "return a + b",
            },
        )
        trace.add(step, self.name, "tool_observation", observation=patch.content, success=patch.success)
        state["modified_files"] = ["examples/demo_repo/src/calculator.py"] if patch.success else []
        state["step"] = step + 1
        return AgentResult(self.name, patch.content)

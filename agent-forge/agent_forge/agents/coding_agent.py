from .base_agent import BaseAgent, AgentResult


class CodingAgent(BaseAgent):
    """Perform the code edit step in the supervised multi-agent demo."""

    name = "CodingAgent"

    def run(self, state):
        """Read the demo file and patch it; retry uses the corrected old text."""

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

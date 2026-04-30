from .base_agent import BaseAgent, AgentResult


class CodingAgent(BaseAgent):
    name = "CodingAgent"

    def run(self, state):
        registry = state["registry"]
        trace = state["trace"]
        step = state.setdefault("step", 10)
        registry.execute("read_file", {"path": "examples/demo_repo/src/calculator.py"})
        patch = registry.execute(
            "apply_patch",
            {
                "path": "examples/demo_repo/src/calculator.py",
                "old": "return a - b",
                "new": "return a + b",
            },
        )
        trace.add(step, self.name, "tool_observation", observation=patch.content, success=patch.success)
        state["modified_files"] = ["examples/demo_repo/src/calculator.py"] if patch.success else []
        state["step"] = step + 1
        return AgentResult(self.name, patch.content)

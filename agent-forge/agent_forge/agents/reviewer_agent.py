from .base_agent import BaseAgent, AgentResult


class ReviewerAgent(BaseAgent):
    name = "ReviewerAgent"

    def run(self, state):
        diff = state["registry"].execute("git_diff", {})
        conclusion = "approved" if state.get("test_pass") else "changes required"
        summary = f"review={conclusion}; diff_len={len(diff.content)}"
        state["review"] = summary
        return AgentResult(self.name, summary)

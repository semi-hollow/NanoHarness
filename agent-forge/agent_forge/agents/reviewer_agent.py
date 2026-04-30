from .base_agent import BaseAgent, AgentResult


class ReviewerAgent(BaseAgent):
    name = "ReviewerAgent"

    def run(self, state):
        diff = state["registry"].execute("git_diff", {})
        modified_files = state.get("modified_files", [])
        if diff.success and diff.content.strip():
            evidence = f"git_diff_len={len(diff.content)}"
        else:
            evidence = f"fallback_modified_files={modified_files}"
        conclusion = "approved" if state.get("test_pass") else "changes required"
        summary = f"review={conclusion}; {evidence}"
        state["review"] = summary
        return AgentResult(self.name, summary)

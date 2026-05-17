from .base_agent import BaseAgent, AgentResult


class ReviewerAgent(BaseAgent):
    """Summarize whether the tested change is acceptable.

    This reviewer is a demo quality gate, not a deep code-review agent. It
    proves where review would sit in the pipeline and records evidence from
    tests/diff. A production reviewer would inspect semantic risk, security,
    backwards compatibility, missing tests, and style rules.
    """

    name = "ReviewerAgent"

    def run(self, state):
        """Use git diff when available, otherwise fall back to modified files.

        The fallback keeps the demo understandable even when git diff is empty
        or unavailable. It is intentionally conservative as trace evidence, not
        a complete review signal.
        """

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

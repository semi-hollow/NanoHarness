from dataclasses import dataclass


@dataclass(frozen=True)
class PlanningStep:
    """Trace-only planning summary for one AgentLoop iteration."""

    goal: str
    reasoning_summary: str
    next_action: str


class SimplePlanner:
    """Small deterministic planner used to make each loop step explainable."""

    def plan(self, task: str, iteration: int, context_report) -> PlanningStep:
        """Summarize current context before asking the LLM for action/final."""

        selected = ", ".join(context_report.selected_files[:3]) if context_report.selected_files else "no selected files"
        return PlanningStep(
            goal=task,
            reasoning_summary=f"iteration={iteration}; selected_context={selected}; chars={context_report.total_chars}",
            next_action="ask llm for final answer or tool call",
        )

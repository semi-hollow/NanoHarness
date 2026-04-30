from dataclasses import dataclass


@dataclass(frozen=True)
class PlanningStep:
    goal: str
    reasoning_summary: str
    next_action: str


class SimplePlanner:
    def plan(self, task: str, iteration: int, context_report) -> PlanningStep:
        selected = ", ".join(context_report.selected_files[:3]) if context_report.selected_files else "no selected files"
        return PlanningStep(
            goal=task,
            reasoning_summary=f"iteration={iteration}; selected_context={selected}; chars={context_report.total_chars}",
            next_action="ask llm for final answer or tool call",
        )

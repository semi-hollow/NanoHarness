from dataclasses import dataclass

from agent_forge.runtime.ports.context import ContextReportView


@dataclass(frozen=True)
class PlanningStep:
    """Trace-only planning summary for one AgentLoop iteration."""

    goal: str
    reasoning_summary: str
    next_action: str


class SimplePlanner:
    """Small deterministic planner used to make each loop step explainable."""

    def plan(
        self,
        task: str,
        iteration: int,
        context_report: ContextReportView,
    ) -> PlanningStep:
        """Summarize current context before asking the LLM for action/final."""

        selected = ", ".join(context_report.selected_files[:3]) if context_report.selected_files else "no selected files"
        strategy = (
            f"topic={context_report.topic_relation}; "
            f"inherit_session={context_report.inherit_session}; "
            f"dropped={len(context_report.dropped_context)}"
        )
        return PlanningStep(
            goal=task,
            reasoning_summary=(
                f"iteration={iteration}; selected_context={selected}; "
                f"chars={context_report.total_chars}; {strategy}"
            ),
            next_action="ask llm for final answer or tool call",
        )

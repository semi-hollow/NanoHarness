from dataclasses import dataclass


@dataclass(frozen=True)
class PlanningModeDecision:
    """Explain whether a run should be ReAct-first or plan-first."""

    # react: decide one tool at a time; plan_execute: make plan then act;
    # workflow: deterministic path is enough; answer_only: no tools needed.
    mode: str

    # Why this mode was selected. Written to trace for audit/debug evidence.
    reason: str

    # Estimated task complexity used by docs and trace.
    complexity: str


class PlanningModePolicy:
    """Select a planning style from the task shape.

    The current AgentLoop remains ReAct-driven, but exposing this policy lets
    the project answer "when use Workflow, Plan-and-Execute, or ReAct?" with
    code evidence instead of only prose.
    """

    def decide(self, task: str) -> PlanningModeDecision:
        """Return the planning mode that best matches the task."""

        lowered = (task or "").lower()
        if any(word in lowered for word in ["explain", "介绍", "讲一下", "说明"]):
            return PlanningModeDecision("answer_only", "question-oriented task; tools may be unnecessary", "low")
        if any(word in lowered for word in ["workflow", "deterministic", "固定流程"]):
            return PlanningModeDecision("workflow", "fixed control flow is acceptable", "low")
        if any(word in lowered for word in ["multi", "多个", "跨文件", "端到端", "benchmark", "swe-bench", "swebench"]):
            return PlanningModeDecision("plan_execute", "multi-step task benefits from explicit plan checkpoints", "high")
        if any(word in lowered for word in ["fix", "修复", "patch", "resolve", "实现", "补充"]):
            return PlanningModeDecision("react", "coding task needs observation-driven tool use", "medium")
        return PlanningModeDecision("react", "default to controlled ReAct for open coding tasks", "medium")

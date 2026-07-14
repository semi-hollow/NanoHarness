from dataclasses import dataclass


@dataclass(frozen=True)
class PlanningModeDecision:

    mode: str
    reason: str
    complexity: str


class PlanningModePolicy:

    def decide(self, task: str) -> PlanningModeDecision:

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

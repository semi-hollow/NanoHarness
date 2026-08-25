"""Multi-Agent Domain 层：计划、结果、依赖、冲突与指标的纯业务规则。"""

from .fanout import (
    CriterionResult,
    FanoutConflict,
    FanoutPlan,
    FinalizerResult,
    FanoutSummary,
    FanoutTaskResult,
    SubagentTask,
    WorkerAttemptResult,
    WorkerHandoff,
    aggregate_fanout_metrics,
    detect_write_scope_conflicts,
    validate_acyclic_dependencies,
)
from .planning import PlannedTask, PlanningDecision
from .live_handoff import LiveDependency, LiveEventType, LiveHandoffEvent

__all__ = [
    "FanoutConflict",
    "FanoutPlan",
    "FinalizerResult",
    "CriterionResult",
    "FanoutSummary",
    "FanoutTaskResult",
    "WorkerAttemptResult",
    "LiveDependency",
    "LiveEventType",
    "LiveHandoffEvent",
    "WorkerHandoff",
    "PlannedTask",
    "PlanningDecision",
    "aggregate_fanout_metrics",
    "SubagentTask",
    "detect_write_scope_conflicts",
    "validate_acyclic_dependencies",
]

"""Multi-Agent 计划、结果和冲突规则。"""

from .fanout import (
    FanoutConflict,
    SubagentResult,
    SubagentTask,
    build_conflict_free_batches,
    build_execution_batches,
    detect_result_conflicts,
    detect_write_scope_conflicts,
)
from .live import (
    CriterionResult,
    FanoutPlan,
    FinalizerResult,
    LiveFanoutSummary,
    LiveSubagentResult,
    WorkerHandoff,
    aggregate_live_metrics,
)
from .planning import PlannedTask, PlanningDecision

__all__ = [
    "FanoutConflict",
    "FanoutPlan",
    "FinalizerResult",
    "CriterionResult",
    "LiveFanoutSummary",
    "LiveSubagentResult",
    "WorkerHandoff",
    "PlannedTask",
    "PlanningDecision",
    "aggregate_live_metrics",
    "SubagentResult",
    "SubagentTask",
    "build_conflict_free_batches",
    "build_execution_batches",
    "detect_result_conflicts",
    "detect_write_scope_conflicts",
]

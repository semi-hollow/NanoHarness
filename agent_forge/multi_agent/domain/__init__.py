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
from .models import (
    AgentProfile,
    Artifact,
    MultiAgentRunSummary,
    RoleRunResult,
    RoleSpec,
)
from .live import (
    FanoutPlan,
    FinalizerResult,
    LiveFanoutSummary,
    LiveSubagentResult,
    aggregate_live_metrics,
)

__all__ = [
    "AgentProfile",
    "Artifact",
    "FanoutConflict",
    "FanoutPlan",
    "FinalizerResult",
    "LiveFanoutSummary",
    "LiveSubagentResult",
    "aggregate_live_metrics",
    "MultiAgentRunSummary",
    "RoleRunResult",
    "RoleSpec",
    "SubagentResult",
    "SubagentTask",
    "build_conflict_free_batches",
    "build_execution_batches",
    "detect_result_conflicts",
    "detect_write_scope_conflicts",
]

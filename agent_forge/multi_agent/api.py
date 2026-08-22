"""Multi-Agent V1 的稳定 import surface。

本文件只 re-export Planner、Coordinator、领域契约和唯一 composition root；没有
业务逻辑或第二条执行链。调用方从这里认识能力，真实控制流从
``build_live_fanout(...)`` 进入 ``FanoutCoordinator.run(...)``。
"""

from .adapters.plan_files import load_fanout_plan, load_resume_initial_plan
from .application.planning import (
    AdaptivePlanner,
    PlanningOutcome,
    resumed_planning_outcome,
    write_planning_artifact,
)
from .application.fanout import FanoutCoordinator
from .application.live_handoff import LiveHandoffRuntime
from .domain.fanout import FanoutConflict, SubagentResult, SubagentTask
from .domain.live import (
    FanoutPlan,
    LiveFanoutSummary,
    LiveSubagentResult,
    WorkerHandoff,
)
from .domain.planning import PlannedTask, PlanningDecision
from .domain.live_handoff import LiveDependency, LiveEventType, LiveHandoffEvent
from .domain.tool_policy import fanout_available_tools
from .wiring import (
    LiveFanoutBuildRequest,
    build_live_fanout,
)

__all__ = [
    "AdaptivePlanner",
    "FanoutConflict",
    "FanoutPlan",
    "FanoutCoordinator",
    "LiveHandoffRuntime",
    "LiveFanoutBuildRequest",
    "LiveFanoutSummary",
    "LiveSubagentResult",
    "LiveDependency",
    "LiveEventType",
    "LiveHandoffEvent",
    "WorkerHandoff",
    "PlannedTask",
    "PlanningDecision",
    "PlanningOutcome",
    "SubagentResult",
    "SubagentTask",
    "build_live_fanout",
    "load_fanout_plan",
    "load_resume_initial_plan",
    "fanout_available_tools",
    "resumed_planning_outcome",
    "write_planning_artifact",
]

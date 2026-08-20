"""Orchestration / Multi-Agent 的公共装配 API。

``build_multi_agent_coordinator(...).run()`` 顺序执行多角色流程；
``build_live_fanout(...).run()`` 按验证后的 DAG 运行隔离 AgentLoop worker；
``build_live_handoff(...).run()`` 为协作 Worker 增加 Runtime 治理的里程碑依赖、
mailbox 和版本新鲜度检查；``build_live_agent_handoff(...).run()`` 复用标准 AgentLoop
安全边界与 worktree substrate 执行真实协作 Worker。
"""

from .adapters.plan_files import load_fanout_plan
from .application.coordinator import MultiAgentCoordinator
from .application.live_fanout import LiveFanoutCoordinator
from .application.live_handoff import LiveHandoffCoordinator
from .domain.fanout import FanoutConflict, SubagentResult, SubagentTask
from .domain.live import FanoutPlan, LiveFanoutSummary, LiveSubagentResult
from .domain.live_handoff import (
    DependencyType,
    HandoffSeverity,
    LiveDependency,
    LiveEventType,
    LiveHandoffEvent,
    LiveHandoffPlan,
    LiveHandoffSummary,
    LiveWorkerCandidate,
    LiveWorkerAttempt,
    LiveWorkerResult,
)
from .domain.models import AgentProfile, MultiAgentRunSummary, RoleSpec
from .wiring import (
    LiveAgentHandoffBuildRequest,
    LiveFanoutBuildRequest,
    LiveHandoffBuildRequest,
    SequentialCoordinatorBuildRequest,
    build_live_agent_handoff,
    build_live_fanout,
    build_live_handoff,
    build_multi_agent_coordinator,
)

__all__ = [
    "AgentProfile",
    "FanoutConflict",
    "FanoutPlan",
    "DependencyType",
    "HandoffSeverity",
    "LiveDependency",
    "LiveEventType",
    "LiveHandoffCoordinator",
    "LiveHandoffEvent",
    "LiveHandoffBuildRequest",
    "LiveAgentHandoffBuildRequest",
    "LiveHandoffPlan",
    "LiveHandoffSummary",
    "LiveFanoutCoordinator",
    "LiveFanoutBuildRequest",
    "LiveFanoutSummary",
    "LiveSubagentResult",
    "LiveWorkerCandidate",
    "LiveWorkerAttempt",
    "LiveWorkerResult",
    "MultiAgentCoordinator",
    "MultiAgentRunSummary",
    "RoleSpec",
    "SequentialCoordinatorBuildRequest",
    "SubagentResult",
    "SubagentTask",
    "build_live_fanout",
    "build_live_agent_handoff",
    "build_live_handoff",
    "build_multi_agent_coordinator",
    "load_fanout_plan",
]

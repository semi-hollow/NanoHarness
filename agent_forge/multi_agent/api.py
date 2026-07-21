"""Orchestration / Multi-Agent 的稳定公共 API。

两条真实主线：``build_multi_agent_coordinator(...).run()`` 顺序执行
Implementer/Reviewer/Verifier；``build_live_fanout(...).run()`` 按验证后的任务 DAG
并发运行真实 AgentLoop worker。DAG、写范围和动态冲突规则由两条主线复用，
不再公开一套只执行注入 callback 的平行调度器。
"""

from .adapters.plan_files import load_fanout_plan
from .application.coordinator import MultiAgentCoordinator
from .application.live_fanout import LiveFanoutCoordinator
from .domain.fanout import FanoutConflict, SubagentResult, SubagentTask
from .domain.live import FanoutPlan, LiveFanoutSummary, LiveSubagentResult
from .domain.models import AgentProfile, MultiAgentRunSummary, RoleSpec
from .wiring import (
    LiveFanoutBuildRequest,
    SequentialCoordinatorBuildRequest,
    build_live_fanout,
    build_multi_agent_coordinator,
)

__all__ = [
    "AgentProfile",
    "FanoutConflict",
    "FanoutPlan",
    "LiveFanoutCoordinator",
    "LiveFanoutBuildRequest",
    "LiveFanoutSummary",
    "LiveSubagentResult",
    "MultiAgentCoordinator",
    "MultiAgentRunSummary",
    "RoleSpec",
    "SequentialCoordinatorBuildRequest",
    "SubagentResult",
    "SubagentTask",
    "build_live_fanout",
    "build_multi_agent_coordinator",
    "load_fanout_plan",
]

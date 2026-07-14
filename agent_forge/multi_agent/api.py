"""Orchestration / Multi-Agent 的稳定公共 API。"""

from .adapters.plan_files import load_fanout_plan
from .application.coordinator import MultiAgentCoordinator
from .application.fanout import run_fanout
from .application.live_fanout import LiveFanoutCoordinator
from .domain.fanout import FanoutConflict, FanoutResult, SubagentResult, SubagentTask
from .domain.live import FanoutPlan, LiveFanoutSummary, LiveSubagentResult
from .domain.models import AgentProfile, MultiAgentRunSummary, RoleSpec
from .wiring import build_live_fanout, build_multi_agent_coordinator

__all__ = [
    "AgentProfile",
    "FanoutConflict",
    "FanoutPlan",
    "FanoutResult",
    "LiveFanoutCoordinator",
    "LiveFanoutSummary",
    "LiveSubagentResult",
    "MultiAgentCoordinator",
    "MultiAgentRunSummary",
    "RoleSpec",
    "SubagentResult",
    "SubagentTask",
    "build_live_fanout",
    "build_multi_agent_coordinator",
    "load_fanout_plan",
    "run_fanout",
]

"""Orchestration / Multi-Agent 的稳定公共 API。

两条真实主线：``build_multi_agent_coordinator(...).run()`` 顺序执行
Implementer/Reviewer/Verifier；``build_live_fanout(...).run()`` 按验证后的任务 DAG
并发运行真实 AgentLoop worker。``run_fanout`` 只是注入 callback 的纯调度器，
主要用于
验证依赖和冲突规则，不能当作真实多 Agent Runtime 展示。
"""

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

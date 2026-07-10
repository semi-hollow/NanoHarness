"""Coordinator-driven multi-agent harness built on top of AgentLoop."""

from .coordinator import MultiAgentCoordinator
from .fanout import FanoutConflict, FanoutResult, SubagentResult, SubagentTask, run_fanout
from .profiles import get_profile, list_profiles
from .types import AgentProfile, Artifact, MultiAgentRunSummary, RoleRunResult, RoleSpec

__all__ = [
    "AgentProfile",
    "Artifact",
    "FanoutConflict",
    "FanoutResult",
    "MultiAgentCoordinator",
    "MultiAgentRunSummary",
    "RoleRunResult",
    "RoleSpec",
    "SubagentResult",
    "SubagentTask",
    "get_profile",
    "list_profiles",
    "run_fanout",
]

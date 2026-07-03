"""Coordinator-driven multi-agent harness built on top of AgentLoop."""

from .coordinator import MultiAgentCoordinator
from .profiles import get_profile, list_profiles
from .types import AgentProfile, Artifact, MultiAgentRunSummary, RoleRunResult, RoleSpec

__all__ = [
    "AgentProfile",
    "Artifact",
    "MultiAgentCoordinator",
    "MultiAgentRunSummary",
    "RoleRunResult",
    "RoleSpec",
    "get_profile",
    "list_profiles",
]

"""兼容导入：Multi-Agent 数据模型已迁移到 domain。"""

from .domain.models import (
    AgentProfile,
    Artifact,
    MultiAgentRunSummary,
    RoleRunResult,
    RoleSpec,
)

__all__ = [
    "AgentProfile",
    "Artifact",
    "MultiAgentRunSummary",
    "RoleRunResult",
    "RoleSpec",
]

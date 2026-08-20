"""Multi-Agent 文件、Git 和执行适配器。"""

from .artifact_files import FileArtifactRepository
from .fanout_files import FanoutFileRepository
from .git_workspace import GitFanoutWorkspace
from .local_worker import LocalAgentWorkerAdapter
from .live_handoff_files import JsonlLiveHandoffRepository
from .live_agent_worker import (
    LiveAgentWorkerAdapter,
    LiveCandidateDiffIntegration,
    LiveHandoffRunControl,
    PublishHandoffEventTool,
)
from .role_runtime import AgentLoopRoleRunner, GitCandidateDiff
from .plan_files import load_fanout_plan

__all__ = [
    "FanoutFileRepository",
    "FileArtifactRepository",
    "GitFanoutWorkspace",
    "LocalAgentWorkerAdapter",
    "JsonlLiveHandoffRepository",
    "LiveAgentWorkerAdapter",
    "LiveCandidateDiffIntegration",
    "LiveHandoffRunControl",
    "PublishHandoffEventTool",
    "AgentLoopRoleRunner",
    "GitCandidateDiff",
    "load_fanout_plan",
]

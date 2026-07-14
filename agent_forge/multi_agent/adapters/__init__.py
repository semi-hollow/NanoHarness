"""Multi-Agent 文件、Git 和执行适配器。"""

from .artifact_files import FileArtifactRepository
from .fanout_files import FanoutFileRepository
from .git_workspace import GitFanoutWorkspace
from .local_worker import LocalAgentWorkerAdapter
from .role_runtime import AgentLoopRoleRunner, GitCandidatePatch
from .plan_files import load_fanout_plan

__all__ = [
    "FanoutFileRepository",
    "FileArtifactRepository",
    "GitFanoutWorkspace",
    "LocalAgentWorkerAdapter",
    "AgentLoopRoleRunner",
    "GitCandidatePatch",
    "load_fanout_plan",
]

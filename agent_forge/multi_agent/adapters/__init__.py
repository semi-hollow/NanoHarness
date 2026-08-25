"""Multi-Agent Adapter 层：实现文件、Git worktree 和真实 AgentLoop 边界。"""

from .fanout_files import FanoutFileRepository
from .git_workspace import GitFanoutWorkspace
from .local_worker import LocalAgentWorkerAdapter
from .plan_files import load_fanout_plan, load_resume_plan

__all__ = [
    "FanoutFileRepository",
    "GitFanoutWorkspace",
    "LocalAgentWorkerAdapter",
    "load_fanout_plan",
    "load_resume_plan",
]

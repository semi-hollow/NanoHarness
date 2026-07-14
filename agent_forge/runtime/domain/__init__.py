"""Runtime 领域模型。

本包只保存运行状态和纯业务语义，不访问文件、网络、Git、进程或具体存储。
"""

from .approval import ApprovalRequest
from .conversation import AgentResponse, Message, Observation, ToolCall
from .governance import ApprovalMode, HookContext, HookDecision, HookDecisionType, HookResult
from .human_input import HumanInputRequest
from .operation import OperationRecord
from .task import TaskCheckpoint, TaskCheckpointData, TaskRunStatus, summarize_checkpoint

__all__ = [
    "ApprovalRequest",
    "AgentResponse",
    "ApprovalMode",
    "HookContext",
    "HookDecision",
    "HookDecisionType",
    "HookResult",
    "HumanInputRequest",
    "Message",
    "Observation",
    "OperationRecord",
    "TaskCheckpoint",
    "TaskCheckpointData",
    "TaskRunStatus",
    "ToolCall",
    "summarize_checkpoint",
]

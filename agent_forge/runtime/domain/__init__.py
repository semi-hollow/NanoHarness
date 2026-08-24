"""Runtime 领域模型。

本包只保存运行状态和纯业务语义，不访问文件、网络、Git、进程或具体存储。
"""

from .approval import ApprovalRequest, ApprovalRequestDraft
from .conversation import AgentResponse, Message, Observation, ToolCall
from .governance import (
    ApprovalMode,
    HookContext,
    HookDecision,
    HookDecisionType,
    HookResult,
)
from .human_input import HumanInputQuestion, HumanInputRequest, HumanInputRequestDraft
from .operation import (
    OperationPlan,
    OperationRecord,
    OperationTarget,
    OperationTransition,
)
from .task import (
    PendingExecutionPointer,
    PendingExecutionPointerData,
    RESUMABLE_RUN_STATUSES,
    TaskCheckpoint,
    TaskCheckpointData,
    TaskCheckpointUpdate,
    TaskRunStatus,
    TaskStartRequest,
)
from .thread import (
    ConversationItem,
    ConversationItemDraft,
    ConversationThread,
    ThreadContextState,
    ThreadRun,
    Turn,
    StableTurnContextSnapshot,
)

__all__ = [
    "ApprovalRequest",
    "ApprovalRequestDraft",
    "AgentResponse",
    "ApprovalMode",
    "HookContext",
    "HookDecision",
    "HookDecisionType",
    "HookResult",
    "HumanInputRequest",
    "HumanInputQuestion",
    "HumanInputRequestDraft",
    "Message",
    "Observation",
    "PendingExecutionPointer",
    "PendingExecutionPointerData",
    "RESUMABLE_RUN_STATUSES",
    "OperationRecord",
    "OperationPlan",
    "OperationTarget",
    "OperationTransition",
    "TaskCheckpoint",
    "TaskCheckpointData",
    "TaskCheckpointUpdate",
    "TaskRunStatus",
    "TaskStartRequest",
    "ConversationItem",
    "ConversationItemDraft",
    "ConversationThread",
    "ThreadContextState",
    "ThreadRun",
    "ToolCall",
    "Turn",
    "StableTurnContextSnapshot",
]

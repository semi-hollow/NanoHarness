"""兼容导入：任务领域状态与 JSON Repository 已拆分。

新代码分别从 ``runtime.domain.task`` 和
``runtime.adapters.task_state_json`` 导入。该模块只保留公开兼容路径。
"""

from agent_forge.runtime.adapters.task_state_json import (
    JsonTaskStateRepository,
    TaskStateStore,
)
from agent_forge.observability.api import replay_trace_file
from agent_forge.runtime.domain.task import (
    TaskCheckpoint,
    TaskCheckpointData,
    TaskRunStatus,
    summarize_checkpoint,
)


def replay_trace(path: str) -> str:
    """兼容 API：trace 展示已归属 Observability capability。"""

    return replay_trace_file(path)

__all__ = [
    "JsonTaskStateRepository",
    "TaskCheckpoint",
    "TaskCheckpointData",
    "TaskRunStatus",
    "TaskStateStore",
    "replay_trace",
    "summarize_checkpoint",
]

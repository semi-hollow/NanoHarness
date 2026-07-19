"""Runtime capability 的内部 facade。

仓库内部入口通过 ``build_agent_loop`` 获取已装配用例；外部嵌入调用应使用顶层
``agent_forge.Harness``，扩展契约从 ``agent_forge.extensions`` 导入。
"""

from agent_forge.runtime.application.agent_loop import AgentLoop
from agent_forge.runtime.application.dependencies import RuntimeDependencies
from agent_forge.runtime.application.operator_control import ContinuationPlan
from agent_forge.runtime.domain.task import TaskCheckpoint, TaskRunStatus
from agent_forge.runtime.wiring import (
    HumanInputResponseCommand,
    ToolRegistryBuildRequest,
    build_agent_loop,
    build_runtime_dependencies,
    decide_approval,
    latest_checkpoint_path,
    list_pending_approvals,
    list_pending_human_inputs,
    load_task_checkpoint,
    prepare_continuation,
    respond_to_human_input,
)

__all__ = [
    "AgentLoop",
    "ContinuationPlan",
    "HumanInputResponseCommand",
    "RuntimeDependencies",
    "TaskCheckpoint",
    "TaskRunStatus",
    "ToolRegistryBuildRequest",
    "build_agent_loop",
    "build_runtime_dependencies",
    "decide_approval",
    "latest_checkpoint_path",
    "list_pending_approvals",
    "list_pending_human_inputs",
    "load_task_checkpoint",
    "prepare_continuation",
    "respond_to_human_input",
]

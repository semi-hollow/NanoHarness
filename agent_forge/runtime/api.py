"""Runtime 的稳定公共 API。

外围模块通过 ``build_agent_loop`` 获取已装配用例；测试也可以直接构造
``RuntimeDependencies`` 注入内存实现。
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

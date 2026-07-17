"""AgentLoop 的显式依赖集合。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_forge.context.ports import LongTermMemoryRecallPort
from agent_forge.runtime.ports import (
    ApprovalRepository,
    ContextAssemblerPort,
    EnvironmentPort,
    EventSink,
    HookPort,
    HumanInputRepository,
    ModelPort,
    OperationLedgerRepository,
    SkillSelectorPort,
    TaskStateRepository,
    ToolGateway,
)


# 核心数据：AgentLoop 依赖的全部能力端口，不包含任何具体 Adapter。
@dataclass(frozen=True)
class RuntimeDependencies:
    """Application 运行所需端口，由 ``runtime.wiring`` 一次性装配。

    ``events`` 写事实，``context/skills/tools/model`` 提供每 turn 输入输出，
    ``environment/hooks`` 治理执行，四个 Repository 保存 checkpoint、审批、
    人工输入和
    operation ledger，``long_term_memory_recall`` 只暴露过滤后的只读召回能力。
    """

    events: EventSink
    context: ContextAssemblerPort
    skills: SkillSelectorPort
    tools: ToolGateway
    model: ModelPort
    environment: EnvironmentPort
    hooks: HookPort
    task_states: TaskStateRepository
    approvals: ApprovalRepository
    human_inputs: HumanInputRepository
    operations: OperationLedgerRepository
    long_term_memory_recall: LongTermMemoryRecallPort

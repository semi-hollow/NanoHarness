"""AgentLoop 的显式依赖集合。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_forge.memory.ports import LongTermMemoryRecallPort
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.ports import (
    ApprovalRepository,
    ConversationThreadRepository,
    TurnSystemContextAssemblerPort,
    EnvironmentPort,
    EventSink,
    HookPort,
    HumanInputRepository,
    ModelPort,
    OperationLedgerRepository,
    RunControlPort,
    SkillSelectorPort,
    TaskStateRepository,
    ToolGateway,
)


# 核心数据：AgentLoop 依赖的全部能力端口，不包含任何具体 Adapter。
@dataclass(frozen=True)
class RuntimeDependencies:
    """Application 运行所需端口，由 ``runtime.wiring`` 一次性装配。

    ``events`` 写事实，Context/Skill/Tool/Model 端口提供每个 Model Step 的输入输出，
    ``environment/hooks`` 治理执行，四个 Repository 保存 checkpoint、审批、
    人工输入和操作状态表，``long_term_memory_recall`` 只暴露 Run-level recall 与
    Turn-level management candidates 的有界只读查询能力。
    """

    events: EventSink
    turn_system_context_assembler: TurnSystemContextAssemblerPort
    skills: SkillSelectorPort
    tools: ToolGateway
    model: ModelPort
    model_capabilities: ModelCapabilities
    environment: EnvironmentPort
    hooks: HookPort
    task_states: TaskStateRepository
    conversation_threads: ConversationThreadRepository
    approvals: ApprovalRepository
    human_inputs: HumanInputRepository
    operations: OperationLedgerRepository
    control: RunControlPort
    long_term_memory_recall: LongTermMemoryRecallPort

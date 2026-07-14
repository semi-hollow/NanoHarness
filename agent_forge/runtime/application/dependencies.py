"""AgentLoop 的显式依赖集合。"""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True)
class RuntimeDependencies:
    """Application 运行所需端口，由 ``runtime.wiring`` 一次性装配。"""

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

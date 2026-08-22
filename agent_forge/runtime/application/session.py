"""一次 Agent run 的显式数据字段；本文件不决定策略。"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_forge.runtime.application.working_memory import WorkingMemory
from agent_forge.observability.domain.evidence import EvidenceLedger
from agent_forge.runtime.application.step_control import StepController
from agent_forge.runtime.domain.conversation import Message, Observation
from agent_forge.runtime.application.run_lifecycle import RunLifecycle
from agent_forge.runtime.ports.skills import SkillView


# 核心数据：AgentLoop 内部唯一的可变 run 状态容器。
@dataclass
class AgentRunSession:
    """一次 ``AgentLoop.run`` 的全部可变状态。

    字段按职责分为：任务身份、生命周期依赖、消息与观察、
    上下文与证据、
    Skill 与
    工具历史、预算和最终状态。这里保存运行数据，不决定策略；控制流在
    ``AgentLoop``，工具治理在 ``ToolExecutionPipeline``，持久化在 ``RunLifecycle``。
    """

    # 不随 turn 改变的任务身份和控制对象。
    task: str
    agent_name: str
    workspace_root: str
    max_iterations: int
    lifecycle: RunLifecycle
    controller: StepController
    # Conversation History：首条 user task 与后续 user/assistant/tool 消息。
    resume_summary: str = ""
    iteration: int = 0
    messages: list[Message] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    # Runtime Context 的重建输入；Tool schemas 由 TurnPreparation 单独路由。
    working_memory: WorkingMemory = field(default_factory=WorkingMemory)
    evidence: EvidenceLedger = field(default_factory=EvidenceLedger)
    active_skills: list[SkillView] = field(default_factory=list)
    skill_tool_names: set[str] = field(default_factory=set)
    # 工具重复检测由 StepController 拥有；Session 只保存验证事实和资源累计。
    ran_tests: bool = False
    blocked: bool = False
    estimated_cost_usd: float = 0.0
    # 返回调用方的本次停止输出；accepted final answer 由 RunLifecycle 质量门决定。
    status: str = "running"
    stop_output: str = ""
    stop_reason: str = ""

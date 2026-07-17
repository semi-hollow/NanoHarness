"""一次 Agent run 的显式数据字段；本文件不决定策略。"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_forge.runtime.application.working_memory import WorkingMemory
from agent_forge.observability.domain.evidence import EvidenceLedger
from agent_forge.runtime.control import StepController
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
    # 可恢复输入和逐 turn 累积状态。
    resume_summary: str = ""
    iteration: int = 0
    messages: list[Message] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    # 三类上下文视图：working memory、可引用 evidence、激活 Skill。
    working_memory: WorkingMemory = field(default_factory=WorkingMemory)
    evidence: EvidenceLedger = field(default_factory=EvidenceLedger)
    active_skills: list[SkillView] = field(default_factory=list)
    skill_tool_names: set[str] = field(default_factory=set)
    # 工具循环防重复、验证事实和运行资源累计。
    tool_history: list[tuple[str, str]] = field(default_factory=list)
    ran_tests: bool = False
    blocked: bool = False
    estimated_cost_usd: float = 0.0
    # 返回调用方和写入 checkpoint 的最终运行状态。
    status: str = "running"
    final_answer: str = ""
    stop_reason: str = ""

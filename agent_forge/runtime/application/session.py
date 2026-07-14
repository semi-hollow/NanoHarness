"""一次 Agent run 的显式数据字段；本文件不决定策略。"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_forge.context.memory import Memory
from agent_forge.observability.domain.evidence import EvidenceLedger
from agent_forge.runtime.control import StepController
from agent_forge.runtime.domain.conversation import Message, Observation
from agent_forge.runtime.application.run_lifecycle import RunLifecycle
from agent_forge.runtime.ports.skills import SkillView


@dataclass
class AgentRunSession:
    """一次 ``AgentLoop.run`` 的全部可变状态。

    第一遍只需看字段名。这里保存运行数据，不决定策略；控制流在
    ``AgentLoop``，工具治理在 ``ToolExecutionPipeline``，持久化在
    ``RunLifecycle``。
    """

    task: str
    agent_name: str
    workspace_root: str
    max_iterations: int
    lifecycle: RunLifecycle
    controller: StepController
    resume_summary: str = ""
    iteration: int = 0
    messages: list[Message] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    memory: Memory = field(default_factory=Memory)
    evidence: EvidenceLedger = field(default_factory=EvidenceLedger)
    active_skills: list[SkillView] = field(default_factory=list)
    skill_tool_names: set[str] = field(default_factory=set)
    tool_history: list[tuple[str, str]] = field(default_factory=list)
    ran_tests: bool = False
    blocked: bool = False
    estimated_cost_usd: float = 0.0
    status: str = "running"
    final_answer: str = ""
    stop_reason: str = ""

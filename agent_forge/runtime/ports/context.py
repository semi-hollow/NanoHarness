"""单次模型 turn 使用的仓库上下文组装端口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent_forge.context.contracts import ContextMemory
from agent_forge.contracts import ToolSchema


# 核心数据：Runtime 请求 Context capability 组装一次模型输入的完整契约。
@dataclass(frozen=True)
class ContextAssemblyRequest:
    """每 turn 的任务、工作区、记忆、工具和治理预算。"""

    task: str
    workspace: str
    working_memory: ContextMemory
    tools: list[ToolSchema]
    active_skill_cards: list[str]
    max_chars: int
    permission_summary: str
    instruction_target: str = ""
    global_instruction_files: tuple[str, ...] = ()
    runtime_instructions: str = ""
    instruction_max_bytes: int = 2_600


class ContextReportView(Protocol):
    """选择和压缩上下文时产生的类型化事实。"""

    selected_files: list[str]
    retrieved_docs: list[str]
    working_memory_summary: str
    long_term_memory: list[str]
    total_chars: int
    max_chars: int
    truncated: bool
    topic_relation: str
    inherit_session: bool
    dropped_context: list[str]
    budget_breakdown: dict[str, int]
    available_tools: list[str]
    permission_summary: str
    instruction_evidence: dict[str, object]

    def render(self) -> str:
        """返回模型可见的 system context。"""


class ContextAssemblerPort(Protocol):
    """隔离仓库扫描和文件预览 IO。"""

    def build(self, request: ContextAssemblyRequest) -> ContextReportView:
        """构造一次有界且可审计的模型上下文。"""

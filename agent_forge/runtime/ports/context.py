"""单次 Model Step 使用的仓库上下文组装端口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent_forge.context.ports.context_memory import ContextMemory
from agent_forge.contracts import JsonObject, ToolSchema


@dataclass(frozen=True, kw_only=True)
class StableTurnContextRequest:
    """新 Turn 首次 attempt 冻结的稳定 Prompt/Skill/Memory 输入。"""

    root_task: str
    workspace: str
    base_tool_schemas: list[ToolSchema]
    active_skill_cards: list[str]
    long_term_memory: list[str]
    max_chars: int
    instruction_target: str = ""
    global_instruction_files: tuple[str, ...] = ()
    runtime_instructions: str = ""
    instruction_max_bytes: int = 2_600
    system_prompt_profile: str = "single_agent"


class StableTurnContextView(Protocol):
    """可写入 Turn snapshot 的稳定前缀与构建证据。"""

    @property
    def total_chars(self) -> int: ...

    @property
    def max_chars(self) -> int: ...

    @property
    def truncated(self) -> bool: ...

    @property
    def dropped_context(self) -> list[str]: ...

    @property
    def budget_breakdown(self) -> dict[str, int]: ...

    @property
    def instruction_evidence(self) -> JsonObject: ...

    @property
    def available_tools(self) -> list[str]: ...

    def render(self) -> str:
        """返回同 Turn 后续 attempts 复用的前缀。"""


# 核心数据：Runtime 请求 Context capability 组装一次模型输入的完整契约。
@dataclass(frozen=True, kw_only=True)
class ModelStepSystemContextRequest:
    """每个 Model Step 的最新焦点、动态状态、工具和独立预算。"""

    turn_focus: str
    stable_system_prefix: str
    workspace: str
    working_memory: ContextMemory
    tool_schemas: list[ToolSchema]
    max_chars: int
    permission_summary: str
    frozen_instruction_paths: tuple[str, ...] = ()


class ModelStepSystemContextView(Protocol):
    """选择和压缩上下文时产生的类型化事实。"""

    selected_files: list[str]
    retrieved_docs: list[str]
    working_memory_summary: str
    total_chars: int
    max_chars: int
    truncated: bool
    dropped_context: list[str]
    budget_breakdown: dict[str, int]
    available_tools: list[str]
    permission_summary: str
    stable_chars: int
    dynamic_chars: int
    dynamic_max_chars: int

    def render(self) -> str:
        """返回模型可见的 system context。"""


class SystemContextAssemblerPort(Protocol):
    """隔离仓库扫描和文件预览 IO。"""

    def freeze_stable(
        self,
        request: StableTurnContextRequest,
    ) -> StableTurnContextView:
        """创建一次 Turn stable-prefix；同 Turn resume 不得再次调用。"""

    def build_model_step(
        self,
        request: ModelStepSystemContextRequest,
    ) -> ModelStepSystemContextView:
        """在冻结前缀之后构造一次有界动态模型上下文。"""

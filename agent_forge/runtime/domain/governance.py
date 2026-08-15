"""生命周期处理器在模型或工具边界返回的领域决策。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class HookDecisionType(Enum):
    """前置处理器对模型调用或工具意图表达的决定。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    DEFER = "defer"


class ApprovalMode(Enum):
    """操作员为一次运行选择的审批姿态。"""

    TRUSTED = "trusted"
    ON_WRITE = "on-write"
    ON_RISK = "on-risk"
    LOCKED = "locked"
    DRY_RUN = "dry-run"


SIDE_EFFECT_ACTIONS = {"write", "run_command", "memory_write"}


@dataclass(frozen=True, kw_only=True)
class HookDecision:
    """一个生命周期处理器的独立决定及审计信息。"""

    hook_name: str
    decision: HookDecisionType
    reason: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """返回稳定 JSON 数据。"""

        return {
            "hook_name": self.hook_name,
            "decision": self.decision.value,
            "reason": self.reason,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, kw_only=True)
class HookContext:
    """工具执行前处理器判断一次工具意图所需的完整上下文。"""

    run_id: str
    step: int
    agent_name: str
    tool_name: str
    arguments: dict
    action: str
    command: str = ""
    auto_approve_writes: bool = True
    approval_mode: str = ApprovalMode.TRUSTED.value


# 核心数据：模型生命周期 Hook 可见的非敏感调用摘要。
@dataclass(frozen=True, kw_only=True)
class ModelHookContext:
    """模型调用身份、规模和压缩状态，不包含 Prompt 正文或凭据。"""

    run_id: str
    step: int
    agent_name: str
    task: str
    messages_count: int
    tool_count: int
    estimated_prompt_tokens: int
    compacted: bool


@dataclass(frozen=True, kw_only=True)
class HookResult:
    """多个前置生命周期处理器合并后的有效决定。"""

    decision: HookDecisionType
    reason: str
    decisions: list[HookDecision]

    def to_dict(self) -> dict:
        """返回 Trace 使用的结构化证据。"""

        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }

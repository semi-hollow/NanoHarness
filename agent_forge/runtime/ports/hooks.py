"""运行时策略 Hook 端口。"""

from __future__ import annotations

from typing import Protocol

from agent_forge.runtime.domain.conversation import AgentResponse, Observation
from agent_forge.runtime.domain.governance import (
    HookContext,
    HookDecision,
    HookResult,
    ModelHookContext,
)
from agent_forge.runtime.domain.task import TaskCheckpoint


class HookPort(Protocol):
    """Application 在模型、工具、checkpoint 和停止边界调用的治理能力。"""

    def before_model(self, context: ModelHookContext) -> HookResult:
        """在模型调用前合并 allow、deny 或 ask 决策。"""

    def after_model(
        self,
        context: ModelHookContext,
        response: AgentResponse,
    ) -> AgentResponse:
        """按注册顺序执行模型响应归一化。"""

    def pre_tool(self, context: HookContext) -> HookResult:
        """在工具执行前给出 allow、deny 或 ask 决策。"""

    def post_tool(
        self,
        context: HookContext,
        observation: Observation,
    ) -> Observation:
        """对工具结果执行脱敏或后置治理。"""

    def on_stop(
        self,
        run_id: str,
        reason: str,
        final_answer: str,
    ) -> list[HookDecision]:
        """记录任务停止时的治理决策。"""

    def on_checkpoint(self, checkpoint: TaskCheckpoint) -> None:
        """在状态已持久化后通知全部 lifecycle hook。"""

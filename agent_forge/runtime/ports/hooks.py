"""运行时策略 Hook 端口。"""

from __future__ import annotations

from typing import Protocol

from agent_forge.runtime.domain.conversation import Observation
from agent_forge.runtime.domain.governance import HookContext, HookDecision, HookResult


class HookPort(Protocol):
    """Application 在工具执行和停止边界调用的治理能力。"""

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

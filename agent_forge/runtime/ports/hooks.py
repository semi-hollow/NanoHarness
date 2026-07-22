"""运行时策略 Hook 端口。

这里是 Application 依赖的 ``Protocol``，只声明“调用方需要什么”，不保存任何逻辑。
真实链路是：使用者 ``RuntimeHook`` -> ``HookManager`` 聚合 -> ``HookPort`` -> Runtime。
Python 允许结构化类型；本项目仍让关键 Adapter 显式继承本协议，方便 IDE 展示层级。
"""

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
    """Runtime 只依赖的聚合 Hook 契约，而不是可实例化的实现。

    默认装配见 ``runtime.wiring._build_runtime_dependencies``，实际实现见
    ``runtime.hooks.HookManager``。单个扩展应继承顶层 ``RuntimeHook``，通常不直接
    实现本协议；Manager 会把多个 ``HookDecision`` 合并为一个 ``HookResult``。
    """

    def before_model(self, context: ModelHookContext) -> HookResult:
        """在模型调用前合并 allow、deny 或 ask 决策。"""

        ...

    def after_model(
        self,
        context: ModelHookContext,
        response: AgentResponse,
    ) -> AgentResponse:
        """按注册顺序执行模型响应归一化。"""

        ...

    def pre_tool(self, context: HookContext) -> HookResult:
        """在工具执行前给出 allow、deny 或 ask 决策。"""

        ...

    def post_tool(
        self,
        context: HookContext,
        observation: Observation,
    ) -> Observation:
        """对工具结果执行脱敏或后置治理。"""

        ...

    def on_stop(
        self,
        run_id: str,
        reason: str,
        final_answer: str,
    ) -> list[HookDecision]:
        """记录任务停止时的治理决策。"""

        ...

    def on_checkpoint(self, checkpoint: TaskCheckpoint) -> None:
        """状态成功持久化后通知扩展；只观察，不修改 checkpoint。"""

        ...

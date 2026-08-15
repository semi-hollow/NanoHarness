"""Runtime 生命周期处理器接口。

代码沿用 ``Hook`` 名称；职责上，它表示 Runtime 到达固定时机时调用的一组处理器。
六个时机是模型前后、工具前后、checkpoint 落盘后和 run 停止前。时机本身不等于
具体规则：例如 ``before_tool`` 只是调用位置，``PermissionHook`` 才负责权限判断。

处理器不拥有 run、审批或 checkpoint 状态。内置处理器和调用方注册的处理器使用同一
接口；“由谁注册”只是扩展方式，不是能力分层。
"""

from __future__ import annotations

from agent_forge.runtime.domain.conversation import AgentResponse, Observation
from agent_forge.runtime.domain.governance import (
    HookContext,
    HookDecision,
    HookDecisionType,
    ModelHookContext,
)
from agent_forge.runtime.domain.task import TaskCheckpoint


class RuntimeHook:
    """六个生命周期时机的无操作默认实现。

    子类只覆盖关心的方法即可。模型/工具前置处理器返回确定性决策；模型/工具后置
    处理器可以归一化结果；checkpoint 处理器只观察已经落盘的状态；stop 处理器可以在
    Runtime 宣称完成前判断是否满足停止条件。
    """

    name = "runtime_hook"

    # 生命周期时机：模型调用前，聚合器会收集各具体处理器的决策。
    def before_model(self, context: ModelHookContext) -> HookDecision:
        """模型调用前的决策时机；默认不表达意见。"""

        return HookDecision(
            hook_name=self.name,
            decision=HookDecisionType.DEFER,
            reason="no hook opinion",
        )

    # 生命周期时机：模型调用后，可按顺序处理响应。
    def after_model(
        self,
        context: ModelHookContext,
        response: AgentResponse,
    ) -> AgentResponse:
        """模型返回后的响应处理时机；默认原样返回。"""

        return response

    # 生命周期时机：工具执行前，聚合器会收集各具体处理器的决策。
    def before_tool(self, context: HookContext) -> HookDecision:
        """工具执行前的决策时机；默认不表达意见。"""

        return HookDecision(
            hook_name=self.name,
            decision=HookDecisionType.DEFER,
            reason="no hook opinion",
        )

    # 生命周期时机：工具执行后，可按顺序处理 Observation。
    def after_tool(
        self,
        context: HookContext,
        observation: Observation,
    ) -> Observation:
        """工具返回后的结果处理时机；默认原样返回。"""

        return observation

    # 生命周期时机：checkpoint 成功持久化后的观察或通知。
    def on_checkpoint(self, checkpoint: TaskCheckpoint) -> None:
        """可选观察点；默认无操作，子类可用于指标、审计或外部通知。

        checkpoint 在调用本方法前已经由 Repository 落盘，所以处理器失败不能撤销或
        篡改状态。当前内置处理器不覆盖它，调用方可按需注册观察处理器。
        """

        return None

    # 生命周期时机：运行停止前，判断是否满足停止条件。
    def on_stop(self, run_id: str, reason: str, stop_output: str) -> HookDecision:
        """停止或完成前的条件判断时机；默认不表达意见。"""

        return HookDecision(
            hook_name=self.name,
            decision=HookDecisionType.DEFER,
            reason=reason,
        )


__all__ = ["RuntimeHook"]

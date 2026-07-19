"""框架使用者可继承的稳定 Runtime Hook 基类。"""

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
    """生命周期扩展点的无操作默认实现。

    子类只覆盖关心的方法即可。工具前置与模型前置返回确定性决策；工具/模型后置
    可以归一化结果；checkpoint 和 stop 用于质量门禁、审计或通知。
    """

    name = "runtime_hook"

    # 主要入口：模型调用前的确定性门禁。
    def before_model(self, context: ModelHookContext) -> HookDecision:
        """模型调用前的确定性门禁；默认不表达意见。"""

        return HookDecision(self.name, HookDecisionType.DEFER, "no hook opinion")

    # 主要入口：模型调用后的响应归一化。
    def after_model(
        self,
        context: ModelHookContext,
        response: AgentResponse,
    ) -> AgentResponse:
        """模型返回后的归一化扩展点；默认原样返回。"""

        return response

    # 主要入口：工具执行前的确定性门禁。
    def before_tool(self, context: HookContext) -> HookDecision:
        """工具执行前的公开门禁扩展点；默认不表达意见。"""

        return HookDecision(self.name, HookDecisionType.DEFER, "no hook opinion")

    def pre_tool(self, context: HookContext) -> HookDecision:
        """兼容内部 Port 命名，并委托给公开 ``before_tool``。"""

        return self.before_tool(context)

    # 主要入口：工具执行后的 Observation 归一化。
    def after_tool(
        self,
        context: HookContext,
        observation: Observation,
    ) -> Observation:
        """工具返回后的公开归一化扩展点；默认原样返回。"""

        return observation

    def post_tool(
        self,
        context: HookContext,
        observation: Observation,
    ) -> Observation:
        """兼容内部 Port 命名，并委托给公开 ``after_tool``。"""

        return self.after_tool(context, observation)

    # 主要入口：checkpoint 成功持久化后的审计或通知。
    def on_checkpoint(self, checkpoint: TaskCheckpoint) -> None:
        """在 checkpoint 已成功持久化后接收不可隐藏的状态事实。"""

    # 主要入口：运行停止前的质量门禁、审计或通知。
    def on_stop(self, run_id: str, reason: str, final_answer: str) -> HookDecision:
        """停止或完成前的质量门禁；默认不表达意见。"""

        return HookDecision(
            self.name,
            HookDecisionType.DEFER,
            reason,
        )


__all__ = ["RuntimeHook"]

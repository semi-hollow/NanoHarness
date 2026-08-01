"""框架使用者可继承的 Runtime 生命周期扩展点。

Hook 是“运行到某个时机时调用外部逻辑”的同步回调，不保存运行状态，也不是
checkpoint。公开生命周期只有六个扩展点：模型前后、工具前后、checkpoint 落盘后和
run 停止前。子类只覆盖自己关心的方法。
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
    """生命周期扩展点的无操作默认实现。

    子类只覆盖关心的方法即可。模型/工具前置 Hook 返回确定性决策；模型/工具后置
    Hook 可以归一化结果；checkpoint Hook 只观察已经落盘的状态；stop Hook 可以在
    Runtime 宣称完成前执行质量门禁。
    """

    name = "runtime_hook"

    # 主要入口：模型调用前的确定性门禁。
    def before_model(self, context: ModelHookContext) -> HookDecision:
        """模型调用前的确定性门禁；默认不表达意见。"""

        return HookDecision(
            hook_name=self.name,
            decision=HookDecisionType.DEFER,
            reason="no hook opinion",
        )

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

        return HookDecision(
            hook_name=self.name,
            decision=HookDecisionType.DEFER,
            reason="no hook opinion",
        )

    # 主要入口：工具执行后的 Observation 归一化。
    def after_tool(
        self,
        context: HookContext,
        observation: Observation,
    ) -> Observation:
        """工具返回后的公开归一化扩展点；默认原样返回。"""

        return observation

    # 主要入口：checkpoint 成功持久化后的审计或通知。
    def on_checkpoint(self, checkpoint: TaskCheckpoint) -> None:
        """可选观察点；默认无操作，子类可用于指标、审计或外部通知。

        checkpoint 在调用本方法前已经由 Repository 落盘，所以 Hook 失败不能撤销或
        篡改状态。Runtime 的内置安全 Hook 无需覆盖它；框架使用者按需覆盖即可。
        """

        return None

    # 主要入口：运行停止前的质量门禁、审计或通知。
    def on_stop(self, run_id: str, reason: str, final_answer: str) -> HookDecision:
        """停止或完成前的质量门禁；默认不表达意见。"""

        return HookDecision(
            hook_name=self.name,
            decision=HookDecisionType.DEFER,
            reason=reason,
        )


__all__ = ["RuntimeHook"]

"""Runtime 内置生命周期处理器与 HookPort 聚合 Adapter。

系统角色：在固定生命周期边界顺序调用内置/扩展 Hook，合并治理决定，并隔离扩展异常。
当前只有三个内置处理器：工具执行前的环境边界检查、权限判断，以及工具执行后的
敏感信息脱敏。``HookManager`` 在六个固定时机调用内置和调用方注册的处理器；它只
负责顺序、决策合并与异常隔离，不拥有审批、Checkpoint 或工具执行状态。

折叠导航：1 permission；2 environment；3 redaction；4 dispatch；5 isolation。
"""

from __future__ import annotations

from functools import partial
from typing import Callable

from agent_forge.hooks import RuntimeHook
from agent_forge.runtime.domain.conversation import AgentResponse, Observation
from agent_forge.runtime.domain.governance import (
    ApprovalMode,
    HookContext,
    HookDecision,
    HookDecisionType,
    HookResult,
    ModelHookContext,
    SIDE_EFFECT_ACTIONS,
)
from agent_forge.runtime.domain.task import TaskCheckpoint
from agent_forge.runtime.adapters.execution_environment import ExecutionEnvironment
from agent_forge.runtime.ports import EventSink, HookPort
from agent_forge.safety.permission import PermissionDecision, PermissionPolicy


# region 1. Permission Hook：把 Tool intent 映射为 ALLOW / ASK / DENY 建议
class PermissionHook(RuntimeHook):
    """在 ``before_tool`` 时机执行动作权限规则并返回统一决策。"""

    name = "permission_policy"

    def __init__(
        self,
        policy: PermissionPolicy,
        approval_mode: str = ApprovalMode.TRUSTED.value,
    ) -> None:
        self.policy = policy
        self.approval_mode = ApprovalMode(approval_mode)

    def before_tool(self, context: HookContext) -> HookDecision:
        permission_decision, permission_reason = self.policy.decide(
            context.action,
            context.command,
        )
        hook_decision_by_permission = {
            PermissionDecision.ALLOW: HookDecisionType.ALLOW,
            PermissionDecision.ASK: HookDecisionType.ASK,
            PermissionDecision.DENY: HookDecisionType.DENY,
        }
        hook_decision = hook_decision_by_permission[permission_decision]
        hook_reason = permission_reason

        if (
            self.approval_mode in {ApprovalMode.LOCKED, ApprovalMode.DRY_RUN}
            and context.action in SIDE_EFFECT_ACTIONS
        ):
            hook_decision = HookDecisionType.DENY
            hook_reason = (
                f"{self.approval_mode.value} approval mode blocks "
                f"side-effect action: {context.action}"
            )
        elif (
            self.approval_mode == ApprovalMode.ON_RISK
            and context.action == "run_command"
            and hook_decision == HookDecisionType.ALLOW
        ):
            hook_decision = HookDecisionType.ASK
            hook_reason = (
                "on-risk approval mode requires approval for command execution"
            )

        return HookDecision(
            hook_name=self.name,
            decision=hook_decision,
            reason=hook_reason,
            metadata={
                "action": context.action,
                "tool_name": context.tool_name,
                "command": context.command,
                "approval_mode": self.approval_mode.value,
                "base_permission_decision": permission_decision.value,
            },
        )
# endregion 1. Permission Hook 结束


# region 2. Environment Hook：把路径/命令边界转成执行前 deny
class ExecutionEnvironmentHook(RuntimeHook):
    """在 ``before_tool`` 时机预检查命令和路径是否越过执行环境边界。"""

    name = "execution_environment"

    def __init__(self, environment: ExecutionEnvironment) -> None:
        self.environment = environment

    def before_tool(self, context: HookContext) -> HookDecision:
        if context.command:
            command_is_allowed, rejection_reason = self.environment.validate_command(
                context.command
            )
            if not command_is_allowed:
                return HookDecision(
                    hook_name=self.name,
                    decision=HookDecisionType.DENY,
                    reason=rejection_reason,
                )

        for path_argument_name in ("path", "file", "target_path"):
            path_argument_value = context.arguments.get(path_argument_name)
            if isinstance(path_argument_value, str):
                path_is_allowed, rejection_reason = self.environment.validate_path(
                    path_argument_value
                )
                if not path_is_allowed:
                    return HookDecision(
                        hook_name=self.name,
                        decision=HookDecisionType.DENY,
                        reason=rejection_reason,
                        metadata={
                            "argument": path_argument_name,
                            "value": path_argument_value,
                        },
                    )

        return HookDecision(
            hook_name=self.name,
            decision=HookDecisionType.DEFER,
            reason="execution environment has no additional restriction",
            metadata={"environment": self.environment.render_boundary_summary()},
        )
# endregion 2. Environment Hook 结束


# region 3. Redaction Hook：工具执行后只改变可见 Observation，不改执行事实
class SecretRedactionHook(RuntimeHook):
    """在 ``after_tool`` 时机对 Observation 做最终凭据脱敏。"""

    name = "secret_redaction"

    def __init__(self, environment: ExecutionEnvironment) -> None:
        self.environment = environment

    def after_tool(
        self,
        context: HookContext,
        observation: Observation,
    ) -> Observation:
        redacted = self.environment.redact(observation.content)
        if redacted == observation.content:
            return observation
        return Observation(
            tool_name=observation.tool_name,
            success=observation.success,
            content=redacted,
        )
# endregion 3. Redaction Hook 结束


# region 4. HookManager：固定顺序 dispatch 并投影六个生命周期点
class HookManager(HookPort):
    """按稳定顺序调用生命周期处理器，合并决策并隔离处理器异常。"""

    def __init__(
        self,
        hooks: list[RuntimeHook] | None = None,
        events: EventSink | None = None,
    ) -> None:
        self.hooks = hooks or []
        self.events = events

    @classmethod
    def default(
        cls,
        environment: ExecutionEnvironment,
        auto_approve_writes: bool = True,
        approval_mode: str = ApprovalMode.TRUSTED.value,
        additional_hooks: list[RuntimeHook] | None = None,
    ) -> "HookManager":
        return cls(
            [
                ExecutionEnvironmentHook(environment),
                PermissionHook(
                    PermissionPolicy(auto_approve_writes),
                    approval_mode=approval_mode,
                ),
                *(additional_hooks or []),
                # 最终脱敏必须位于使用者自定义的工具后置 Hook 之后。
                SecretRedactionHook(environment),
            ]
        )

    def observe_with(self, events: EventSink) -> "HookManager":
        """由 composition root 绑定处理器异常证据，不扩大策略工厂签名。"""

        self.events = events
        return self

    def before_model(self, context: ModelHookContext) -> HookResult:
        """在 ``before_model`` 时机按拒绝优先级合并具体处理器决策。"""

        hook_decisions = [
            self._safe_decision(
                hook,
                "before_model",
                context.step,
                context.agent_name,
                partial(hook.before_model, context),
            )
            for hook in self.hooks
        ]
        return self._merge(hook_decisions)

    def after_model(
        self,
        context: ModelHookContext,
        response: AgentResponse,
    ) -> AgentResponse:
        """在 ``after_model`` 时机按注册顺序处理模型响应。"""

        current_model_response = response
        for hook in self.hooks:
            try:
                current_model_response = hook.after_model(
                    context,
                    current_model_response,
                )
            except Exception as exc:
                self._record_failure(
                    hook,
                    "after_model",
                    exc,
                    context.step,
                    context.agent_name,
                )
        return current_model_response

    # 运行时端口：在 before_tool 时机按拒绝优先级合并具体处理器决策。
    def before_tool(self, context: HookContext) -> HookResult:
        """调用所有 ``before_tool`` 处理器，并按 DENY > ASK > ALLOW > DEFER 合并。

        每项判断都保留为证据；决策型处理器异常按 DENY 处理。本方法不读取人工审批，
        也不执行 Tool，后续由 ``ToolAuthorizationGate`` 完成授权收口。
        """

        hook_decisions = [
            self._safe_decision(
                hook,
                "before_tool",
                context.step,
                context.agent_name,
                partial(hook.before_tool, context),
            )
            for hook in self.hooks
        ]
        return self._merge(hook_decisions)

    @staticmethod
    def _merge(hook_decisions: list[HookDecision]) -> HookResult:
        """DENY > ASK > ALLOW > DEFER，所有独立决定均保留为证据。"""

        for hook_decision in hook_decisions:
            if hook_decision.decision == HookDecisionType.DENY:
                return HookResult(
                    decision=HookDecisionType.DENY,
                    reason=hook_decision.reason,
                    decisions=hook_decisions,
                )
        for hook_decision in hook_decisions:
            if hook_decision.decision == HookDecisionType.ASK:
                return HookResult(
                    decision=HookDecisionType.ASK,
                    reason=hook_decision.reason,
                    decisions=hook_decisions,
                )
        for hook_decision in hook_decisions:
            if hook_decision.decision == HookDecisionType.ALLOW:
                return HookResult(
                    decision=HookDecisionType.ALLOW,
                    reason=hook_decision.reason,
                    decisions=hook_decisions,
                )
        return HookResult(
            decision=HookDecisionType.ALLOW,
            reason="all hooks deferred; default allow",
            decisions=hook_decisions,
        )

    def after_tool(
        self,
        context: HookContext,
        observation: Observation,
    ) -> Observation:
        """在 ``after_tool`` 时机按注册顺序处理结果，最后执行敏感信息脱敏。"""

        current_observation = observation
        for hook in self.hooks:
            try:
                current_observation = hook.after_tool(
                    context,
                    current_observation,
                )
            except Exception as exc:
                self._record_failure(
                    hook,
                    "after_tool",
                    exc,
                    context.step,
                    context.agent_name,
                )
        return current_observation

    def on_stop(
        self, run_id: str, reason: str, stop_output: str
    ) -> list[HookDecision]:
        return [
            self._safe_decision(
                hook,
                "on_stop",
                0,
                "Runtime",
                partial(hook.on_stop, run_id, reason, stop_output),
            )
            for hook in self.hooks
        ]

    # HookPort 的真实聚合实现：checkpoint 落盘后逐个通知生命周期处理器。
    def on_checkpoint(self, checkpoint: TaskCheckpoint) -> None:
        """checkpoint 已落盘后依次通知处理器；处理器不拥有该状态。"""

        for hook in self.hooks:
            try:
                hook.on_checkpoint(checkpoint)
            except Exception as exc:
                self._record_failure(
                    hook,
                    "on_checkpoint",
                    exc,
                    checkpoint.current_step,
                    checkpoint.agent_name,
                )
    # endregion 4. HookManager dispatch 结束

    # region 5. 异常隔离与证据：decision Hook fail closed，notification Hook 只记录
    def _safe_decision(
        self,
        hook: RuntimeHook,
        hook_stage: str,
        step: int,
        agent_name: str,
        invoke_hook: Callable[[], HookDecision],
    ) -> HookDecision:
        """决策型处理器异常时 fail closed，而不是跳过确定性规则。"""

        try:
            return invoke_hook()
        except Exception as exc:
            self._record_failure(hook, hook_stage, exc, step, agent_name)
            return HookDecision(
                hook_name=hook.name,
                decision=HookDecisionType.DENY,
                reason=f"hook failed during {hook_stage}",
                metadata={"error_type": type(exc).__name__},
            )

    def _record_failure(
        self,
        hook: RuntimeHook,
        hook_stage: str,
        error: Exception,
        step: int,
        agent_name: str,
    ) -> None:
        """只记录异常类型和短消息，不把处理器内部对象写入 evidence。"""

        if self.events is None:
            return
        self.events.add(
            step,
            agent_name,
            "hook_check",
            success=False,
            error=type(error).__name__,
            hook_stage=hook_stage,
            hook_name=hook.name,
            failure_policy=(
                "fail_closed"
                if hook_stage in {"before_model", "before_tool", "on_stop"}
                else "isolated"
            ),
        )
    # endregion 5. 异常隔离与证据结束

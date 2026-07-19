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
from agent_forge.runtime.execution_environment import ExecutionEnvironment
from agent_forge.runtime.ports import EventSink
from agent_forge.safety.permission import PermissionDecision, PermissionPolicy


class PermissionHook(RuntimeHook):
    name = "permission_policy"

    def __init__(
        self,
        policy: PermissionPolicy,
        approval_mode: str = ApprovalMode.TRUSTED.value,
    ) -> None:
        self.policy = policy
        self.approval_mode = ApprovalMode(approval_mode)

    def pre_tool(self, context: HookContext) -> HookDecision:
        decision, reason = self.policy.decide(context.action, context.command)
        mapping = {
            PermissionDecision.ALLOW: HookDecisionType.ALLOW,
            PermissionDecision.ASK: HookDecisionType.ASK,
            PermissionDecision.DENY: HookDecisionType.DENY,
        }
        hook_decision = mapping[decision]
        hook_reason = reason

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
            hook_reason = "on-risk approval mode requires approval for command execution"

        return HookDecision(
            self.name,
            hook_decision,
            hook_reason,
            {
                "action": context.action,
                "tool_name": context.tool_name,
                "command": context.command,
                "approval_mode": self.approval_mode.value,
                "base_permission_decision": decision.value,
            },
        )


class ExecutionEnvironmentHook(RuntimeHook):
    name = "execution_environment"

    def __init__(self, environment: ExecutionEnvironment) -> None:
        self.environment = environment

    def pre_tool(self, context: HookContext) -> HookDecision:
        if context.command:
            ok, reason = self.environment.validate_command(context.command)
            if not ok:
                return HookDecision(self.name, HookDecisionType.DENY, reason)

        for key in ("path", "file", "target_path"):
            value = context.arguments.get(key)
            if isinstance(value, str):
                ok, reason = self.environment.validate_path(value)
                if not ok:
                    return HookDecision(
                        self.name,
                        HookDecisionType.DENY,
                        reason,
                        {"argument": key, "value": value},
                    )

        return HookDecision(
            self.name,
            HookDecisionType.DEFER,
            "execution environment has no additional restriction",
            {"environment": self.environment.describe()},
        )


class SecretRedactionHook(RuntimeHook):
    name = "secret_redaction"

    def __init__(self, environment: ExecutionEnvironment) -> None:
        self.environment = environment

    def post_tool(
        self,
        context: HookContext,
        observation: Observation,
    ) -> Observation:
        redacted = self.environment.redact(observation.content)
        if redacted == observation.content:
            return observation
        return Observation(observation.tool_name, observation.success, redacted)


class HookManager:
    """按稳定顺序组合安全 Hook 与使用者 Hook，并隔离扩展异常。"""

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
                # 最终脱敏必须位于自定义 post-tool Hook 之后。
                SecretRedactionHook(environment),
            ]
        )

    def observe_with(self, events: EventSink) -> "HookManager":
        """由 composition root 绑定 Hook 异常证据，不扩大策略工厂签名。"""

        self.events = events
        return self

    def before_model(self, context: ModelHookContext) -> HookResult:
        """按拒绝优先级合并全部模型前置 Hook。"""

        decisions = [
            self._safe_decision(
                hook,
                "before_model",
                context.step,
                context.agent_name,
                partial(hook.before_model, context),
            )
            for hook in self.hooks
        ]
        return self._merge(decisions)

    def after_model(
        self,
        context: ModelHookContext,
        response: AgentResponse,
    ) -> AgentResponse:
        """按注册顺序应用模型响应后置 Hook。"""

        current = response
        for hook in self.hooks:
            try:
                current = hook.after_model(context, current)
            except Exception as exc:
                self._record_failure(
                    hook,
                    "after_model",
                    exc,
                    context.step,
                    context.agent_name,
                )
        return current

    # 运行时端口：按拒绝优先级合并所有治理 Hook 的工具前置决策。
    def pre_tool(self, context: HookContext) -> HookResult:
        decisions = [
            self._safe_decision(
                hook,
                "before_tool",
                context.step,
                context.agent_name,
                partial(hook.pre_tool, context),
            )
            for hook in self.hooks
        ]
        return self._merge(decisions)

    @staticmethod
    def _merge(decisions: list[HookDecision]) -> HookResult:
        """DENY > ASK > ALLOW > DEFER，所有独立决定均保留为证据。"""

        for decision in decisions:
            if decision.decision == HookDecisionType.DENY:
                return HookResult(HookDecisionType.DENY, decision.reason, decisions)
        for decision in decisions:
            if decision.decision == HookDecisionType.ASK:
                return HookResult(HookDecisionType.ASK, decision.reason, decisions)
        for decision in decisions:
            if decision.decision == HookDecisionType.ALLOW:
                return HookResult(HookDecisionType.ALLOW, decision.reason, decisions)
        return HookResult(HookDecisionType.ALLOW, "all hooks deferred; default allow", decisions)

    def post_tool(self, context: HookContext, observation: Observation) -> Observation:
        current = observation
        for hook in self.hooks:
            try:
                current = hook.post_tool(context, current)
            except Exception as exc:
                self._record_failure(
                    hook,
                    "after_tool",
                    exc,
                    context.step,
                    context.agent_name,
                )
        return current

    def on_stop(self, run_id: str, reason: str, final_answer: str) -> list[HookDecision]:
        return [
            self._safe_decision(
                hook,
                "on_stop",
                0,
                "Runtime",
                partial(hook.on_stop, run_id, reason, final_answer),
            )
            for hook in self.hooks
        ]

    def on_checkpoint(self, checkpoint: TaskCheckpoint) -> None:
        """checkpoint 已落盘后依次通知 Hook。"""

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

    def _safe_decision(
        self,
        hook: RuntimeHook,
        stage: str,
        step: int,
        agent_name: str,
        invoke: Callable[[], HookDecision],
    ) -> HookDecision:
        """前置和完成门禁异常时 fail closed，而不是跳过确定性策略。"""

        try:
            return invoke()
        except Exception as exc:
            self._record_failure(hook, stage, exc, step, agent_name)
            return HookDecision(
                hook.name,
                HookDecisionType.DENY,
                f"hook failed during {stage}",
                {"error_type": type(exc).__name__},
            )

    def _record_failure(
        self,
        hook: RuntimeHook,
        stage: str,
        error: Exception,
        step: int,
        agent_name: str,
    ) -> None:
        """只记录异常类型和短消息，不把 Hook 内部对象写入 evidence。"""

        if self.events is None:
            return
        self.events.add(
            step,
            agent_name,
            "hook_check",
            success=False,
            error=type(error).__name__,
            hook_stage=stage,
            hook_name=hook.name,
            failure_policy=(
                "fail_closed"
                if stage in {"before_model", "before_tool", "on_stop"}
                else "isolated"
            ),
        )

from agent_forge.runtime.domain.governance import (
    ApprovalMode,
    HookContext,
    HookDecision,
    HookDecisionType,
    HookResult,
    SIDE_EFFECT_ACTIONS,
)
from agent_forge.runtime.execution_environment import ExecutionEnvironment
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.permission import PermissionDecision, PermissionPolicy


class RuntimeHook:

    name = "runtime_hook"

    def pre_tool(self, context: HookContext) -> HookDecision:

        return HookDecision(self.name, HookDecisionType.DEFER, "no hook opinion")

    def post_tool(self, context: HookContext, observation: Observation) -> Observation:

        return observation

    def on_stop(self, run_id: str, reason: str, final_answer: str) -> HookDecision:

        return HookDecision(self.name, HookDecisionType.DEFER, reason, {"final_answer": final_answer[:300]})


class PermissionHook(RuntimeHook):

    name = "permission_policy"

    def __init__(self, policy: PermissionPolicy, approval_mode: str = ApprovalMode.TRUSTED.value) -> None:

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

        if self.approval_mode in {ApprovalMode.LOCKED, ApprovalMode.DRY_RUN} and context.action in SIDE_EFFECT_ACTIONS:
            hook_decision = HookDecisionType.DENY
            hook_reason = f"{self.approval_mode.value} approval mode blocks side-effect action: {context.action}"
        elif self.approval_mode == ApprovalMode.ON_RISK and context.action == "run_command" and hook_decision == HookDecisionType.ALLOW:
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

    def post_tool(self, context: HookContext, observation: Observation) -> Observation:

        redacted = self.environment.redact(observation.content)
        if redacted == observation.content:
            return observation
        return Observation(observation.tool_name, observation.success, redacted)


class HookManager:

    def __init__(self, hooks: list[RuntimeHook] | None = None) -> None:

        self.hooks = hooks or []

    @classmethod
    def default(
        cls,
        environment: ExecutionEnvironment,
        auto_approve_writes: bool = True,
        approval_mode: str = ApprovalMode.TRUSTED.value,
    ) -> "HookManager":

        return cls(
            [
                ExecutionEnvironmentHook(environment),
                PermissionHook(PermissionPolicy(auto_approve_writes), approval_mode=approval_mode),
                SecretRedactionHook(environment),
            ]
        )

    # 运行时端口：按拒绝优先级合并所有治理 Hook 的工具前置决策。
    def pre_tool(self, context: HookContext) -> HookResult:

        decisions = [hook.pre_tool(context) for hook in self.hooks]
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
            current = hook.post_tool(context, current)
        return current

    def on_stop(self, run_id: str, reason: str, final_answer: str) -> list[HookDecision]:

        return [hook.on_stop(run_id, reason, final_answer) for hook in self.hooks]

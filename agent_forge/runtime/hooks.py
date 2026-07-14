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
    """Base hook contract.

    Hooks are the extension point for policy that should run outside model
    prompts: permission checks, environment checks, secret redaction, audit
    enrichment, and eventually product-specific approvals.
    """

    name = "runtime_hook"

    def pre_tool(self, context: HookContext) -> HookDecision:
        """Inspect a proposed tool call before execution."""

        return HookDecision(self.name, HookDecisionType.DEFER, "no hook opinion")

    def post_tool(self, context: HookContext, observation: Observation) -> Observation:
        """Inspect or transform a tool observation after execution."""

        return observation

    def on_stop(self, run_id: str, reason: str, final_answer: str) -> HookDecision:
        """Observe a run stop event."""

        return HookDecision(self.name, HookDecisionType.DEFER, reason, {"final_answer": final_answer[:300]})


class PermissionHook(RuntimeHook):
    """Deterministic permission policy hook.

    This wraps ``PermissionPolicy`` so AgentLoop does not special-case
    read/write/command logic. Prompt text can suggest behavior, but this hook is
    the actual gate.
    """

    name = "permission_policy"

    def __init__(self, policy: PermissionPolicy, approval_mode: str = ApprovalMode.TRUSTED.value) -> None:
        """Store the concrete permission policy for this run."""

        self.policy = policy
        self.approval_mode = ApprovalMode(approval_mode)

    def pre_tool(self, context: HookContext) -> HookDecision:
        """Map PermissionPolicy decisions into hook decisions."""

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
    """Hook that enforces execution-environment boundaries."""

    name = "execution_environment"

    def __init__(self, environment: ExecutionEnvironment) -> None:
        """Store the active environment for path/command checks."""

        self.environment = environment

    def pre_tool(self, context: HookContext) -> HookDecision:
        """Reject paths or commands that escape the configured environment."""

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
    """Post-tool hook that keeps credentials out of observations and trace."""

    name = "secret_redaction"

    def __init__(self, environment: ExecutionEnvironment) -> None:
        """Use the environment's redaction policy."""

        self.environment = environment

    def post_tool(self, context: HookContext, observation: Observation) -> Observation:
        """Return a redacted observation when tool output contains secrets."""

        redacted = self.environment.redact(observation.content)
        if redacted == observation.content:
            return observation
        return Observation(observation.tool_name, observation.success, redacted)


class HookManager:
    """Ordered runtime hook runner.

    The manager's effective-decision rule is conservative: any deny wins, ask
    beats allow, and allow is used only when at least one hook explicitly allows
    and no hook blocks. If every hook defers, the tool is allowed so read-only
    extension hooks can remain optional.

    Why it exists:
        Tool implementations should not each reimplement approval, execution
        environment checks, and redaction. HookManager gives the runtime one
        ordered policy chain that can be inspected in trace.

    Method map:
        ``default`` builds the standard environment + permission + redaction
        chain.
        ``pre_tool`` decides allow/ask/deny before execution.
        ``post_tool`` transforms observations, mainly secret redaction.
        ``on_stop`` lets hooks record terminal audit decisions.
    """

    def __init__(self, hooks: list[RuntimeHook] | None = None) -> None:
        """Keep hook order deterministic for trace review."""

        self.hooks = hooks or []

    @classmethod
    def default(
        cls,
        environment: ExecutionEnvironment,
        auto_approve_writes: bool = True,
        approval_mode: str = ApprovalMode.TRUSTED.value,
    ) -> "HookManager":
        """Build the default local production-style hook chain."""

        return cls(
            [
                ExecutionEnvironmentHook(environment),
                PermissionHook(PermissionPolicy(auto_approve_writes), approval_mode=approval_mode),
                SecretRedactionHook(environment),
            ]
        )

    # RUNTIME PORT: ToolExecutionPipeline asks the policy chain before every tool.
    def pre_tool(self, context: HookContext) -> HookResult:
        """Return the effective allow, ask, or deny decision before execution."""

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
        """Run post-tool hooks in order, carrying transformed observations."""

        current = observation
        for hook in self.hooks:
            current = hook.post_tool(context, current)
        return current

    def on_stop(self, run_id: str, reason: str, final_answer: str) -> list[HookDecision]:
        """Notify hooks that the run stopped."""

        return [hook.on_stop(run_id, reason, final_answer) for hook in self.hooks]

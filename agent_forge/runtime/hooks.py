from dataclasses import dataclass, field
from enum import Enum

from agent_forge.runtime.execution_environment import ExecutionEnvironment
from agent_forge.runtime.observation import Observation
from agent_forge.safety.permission import PermissionDecision, PermissionPolicy


class HookDecisionType(Enum):
    """Decision vocabulary for runtime hooks.

    ``DEFER`` means "this hook has no opinion"; the next hook or the default
    policy decides. ``ASK`` is important because write tools can be allowed only
    after an explicit approval step is recorded in trace.
    """

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    DEFER = "defer"


@dataclass(frozen=True)
class HookDecision:
    """One hook's decision and audit metadata."""

    # Name of the hook that produced the decision.
    hook_name: str

    # allow/deny/ask/defer.
    decision: HookDecisionType

    # Human-readable reason copied into trace and task-state checkpoints.
    reason: str

    # Extra structured context for reports.
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Return JSON-safe trace data."""

        return {
            "hook_name": self.hook_name,
            "decision": self.decision.value,
            "reason": self.reason,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class HookContext:
    """Runtime facts available to hooks before a tool runs."""

    # Trace run id.
    run_id: str

    # ReAct step number.
    step: int

    # Agent/role requesting the action.
    agent_name: str

    # Concrete tool name requested by the model.
    tool_name: str

    # JSON arguments after provider parsing.
    arguments: dict

    # Coarse action class: read, apply_patch, run_command.
    action: str

    # Shell command for run_command tools; empty otherwise.
    command: str = ""

    # Whether ASK decisions can auto-approve in this local run.
    auto_approve_writes: bool = True


@dataclass(frozen=True)
class HookResult:
    """Effective pre-tool decision after all hooks have run."""

    # Final decision used by AgentLoop.
    decision: HookDecisionType

    # Reason for the final decision.
    reason: str

    # Per-hook evidence.
    decisions: list[HookDecision]

    def to_dict(self) -> dict:
        """Return JSON-safe trace data."""

        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


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

    def __init__(self, policy: PermissionPolicy):
        """Store the concrete permission policy for this run."""

        self.policy = policy

    def pre_tool(self, context: HookContext) -> HookDecision:
        """Map PermissionPolicy decisions into hook decisions."""

        decision, reason = self.policy.decide(context.action, context.command)
        mapping = {
            PermissionDecision.ALLOW: HookDecisionType.ALLOW,
            PermissionDecision.ASK: HookDecisionType.ASK,
            PermissionDecision.DENY: HookDecisionType.DENY,
        }
        return HookDecision(
            self.name,
            mapping[decision],
            reason,
            {
                "action": context.action,
                "tool_name": context.tool_name,
                "command": context.command,
            },
        )


class ExecutionEnvironmentHook(RuntimeHook):
    """Hook that enforces execution-environment boundaries."""

    name = "execution_environment"

    def __init__(self, environment: ExecutionEnvironment):
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

    def __init__(self, environment: ExecutionEnvironment):
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
    """

    def __init__(self, hooks: list[RuntimeHook] | None = None):
        """Keep hook order deterministic for trace review."""

        self.hooks = hooks or []

    @classmethod
    def default(cls, environment: ExecutionEnvironment, auto_approve_writes: bool = True) -> "HookManager":
        """Build the default local production-style hook chain."""

        return cls(
            [
                ExecutionEnvironmentHook(environment),
                PermissionHook(PermissionPolicy(auto_approve_writes)),
                SecretRedactionHook(environment),
            ]
        )

    def pre_tool(self, context: HookContext) -> HookResult:
        """Run every pre-tool hook and return the effective decision."""

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

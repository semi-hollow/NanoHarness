import json
import time
from dataclasses import dataclass, field
from enum import Enum

from agent_forge.runtime.observation import Observation
from agent_forge.runtime.tool_call import ToolCall


class FailureKind(Enum):
    """Failure classes the runtime can react to differently."""

    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    PERMISSION_DENIED = "permission_denied"
    PATCH_MISMATCH = "patch_mismatch"
    COMMAND_FAILED = "command_failed"
    TOOL_EXCEPTION = "tool_exception"
    REPEATED_ACTION = "repeated_action"
    MODEL_RESPONSE = "model_response"
    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass(frozen=True)
class FailureSignal:
    """Normalized failure signal emitted by tools, model calls, or loop policy."""

    kind: FailureKind
    reason: str
    retryable: bool
    recovery_hint: str


@dataclass(frozen=True)
class ExecutionBudget:
    """Runtime limits that keep an autonomous loop controllable."""

    max_steps: int = 12
    max_consecutive_failures: int = 3
    max_tool_repeats: int = 2
    timeout_seconds: float = 120.0
    cost_budget_usd: float | None = None


@dataclass
class StepController:
    """Control-plane state for one AgentLoop run.

    AgentLoop should express the ReAct flow, not hide every policy branch.
    This controller owns the production concerns interviewers care about:
    repeated-action detection, retryability classification, timeout, cost, and
    recovery hints for the next LLM turn.
    """

    budget: ExecutionBudget
    started_at: float = field(default_factory=time.time)
    tool_counts: dict[str, int] = field(default_factory=dict)
    failure_count: int = 0

    @classmethod
    def from_config(cls, config) -> "StepController":
        """Build controller limits from RuntimeConfig with safe defaults."""

        return cls(
            ExecutionBudget(
                max_steps=config.max_steps,
                max_consecutive_failures=getattr(config, "max_consecutive_failures", 3),
                max_tool_repeats=getattr(config, "max_tool_repeats", 2),
                timeout_seconds=getattr(config, "timeout_seconds", 120.0),
                cost_budget_usd=getattr(config, "cost_budget_usd", None),
            )
        )

    def record_tool_intent(self, tool_call: ToolCall) -> FailureSignal | None:
        """Return a repeated-action failure before the tool is executed."""

        key = self._tool_key(tool_call)
        self.tool_counts[key] = self.tool_counts.get(key, 0) + 1
        if self.tool_counts[key] > self.budget.max_tool_repeats:
            return FailureSignal(
                FailureKind.REPEATED_ACTION,
                f"repeated tool call: {tool_call.name}",
                retryable=False,
                recovery_hint="Stop the loop or choose a materially different action with new evidence.",
            )
        return None

    def classify_observation(self, observation: Observation) -> FailureSignal | None:
        """Map a raw Observation into retryability and recovery guidance."""

        if observation.success:
            self.failure_count = 0
            return None

        self.failure_count += 1
        content = observation.content.lower()
        if "unknown tool" in content or "not allowed for this agent" in content:
            return FailureSignal(
                FailureKind.UNKNOWN_TOOL,
                observation.content,
                retryable=False,
                recovery_hint="Use only tools exposed in available_tools or stop with a clear limitation.",
            )
        if "invalid arguments" in content or "missing" in content:
            return FailureSignal(
                FailureKind.INVALID_ARGUMENTS,
                observation.content,
                retryable=True,
                recovery_hint="Repair the tool arguments using the tool schema and retry once.",
            )
        if "old text not found" in content:
            return FailureSignal(
                FailureKind.PATCH_MISMATCH,
                observation.content,
                retryable=True,
                recovery_hint="Re-read the target file, update the patch anchor, then retry.",
            )
        if "denied" in content or "blocked" in content or "needs_approval" in content:
            return FailureSignal(
                FailureKind.PERMISSION_DENIED,
                observation.content,
                retryable=False,
                recovery_hint="Do not bypass policy; ask for approval or report the blocked action.",
            )
        if "exit_code=" in content and "exit_code=0" not in content:
            return FailureSignal(
                FailureKind.COMMAND_FAILED,
                observation.content,
                retryable=True,
                recovery_hint="Inspect the failure output, patch the root cause, and rerun the smallest validation.",
            )
        return FailureSignal(
            FailureKind.TOOL_EXCEPTION,
            observation.content,
            retryable=True,
            recovery_hint="Use the observation text as evidence, adjust the next action, and avoid repeating blindly.",
        )

    def should_stop(self, step: int, estimated_cost_usd: float = 0.0) -> FailureSignal | None:
        """Return the first budget stop signal, if any."""

        if step >= self.budget.max_steps:
            return FailureSignal(
                FailureKind.BUDGET_EXCEEDED,
                "max_steps reached",
                retryable=False,
                recovery_hint="Summarize current state and stop rather than continuing indefinitely.",
            )
        if self.failure_count >= self.budget.max_consecutive_failures:
            return FailureSignal(
                FailureKind.BUDGET_EXCEEDED,
                "too many consecutive failed tools",
                retryable=False,
                recovery_hint="Stop and report the failure chain instead of looping.",
            )
        if time.time() - self.started_at > self.budget.timeout_seconds:
            return FailureSignal(
                FailureKind.BUDGET_EXCEEDED,
                "timeout exceeded",
                retryable=False,
                recovery_hint="Stop and preserve enough state for a later resume.",
            )
        if self.budget.cost_budget_usd is not None and estimated_cost_usd > self.budget.cost_budget_usd:
            return FailureSignal(
                FailureKind.BUDGET_EXCEEDED,
                "cost budget exceeded",
                retryable=False,
                recovery_hint="Stop before spending more model budget.",
            )
        return None

    def model_failure(self, error: dict) -> FailureSignal:
        """Normalize invalid or failed LLM responses."""

        code = str(error.get("code") or error.get("type") or "model_error")
        return FailureSignal(
            FailureKind.MODEL_RESPONSE,
            code,
            retryable=code in {"request_failed", "temporary_failure", "timeout"},
            recovery_hint="Retry through ModelGateway fallback if configured; otherwise stop with provider diagnostics.",
        )

    def _tool_key(self, tool_call: ToolCall) -> str:
        """Build a stable identity for repeated-action detection."""

        try:
            args = json.dumps(tool_call.arguments or {}, ensure_ascii=False, sort_keys=True)
        except TypeError:
            args = str(tool_call.arguments)
        return f"{tool_call.name}:{args}"

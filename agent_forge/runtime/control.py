"""AgentLoop 的预算、重复动作检测和失败恢复分类。

可以把 ``StepController`` 理解为 Java 业务流程中的轻量运行控制器：它只返回
``FailureSignal`` 决策，不执行工具、不落 checkpoint，也不负责最终失败分类报告。
"""

import json
import time
from dataclasses import dataclass, field
from enum import Enum

from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import Observation, ToolCall


class FailureKind(Enum):
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    PERMISSION_DENIED = "permission_denied"
    PATCH_MISMATCH = "patch_mismatch"
    COMMAND_FAILED = "command_failed"
    TOOL_EXCEPTION = "tool_exception"
    REPEATED_ACTION = "repeated_action"
    MODEL_RESPONSE = "model_response"
    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass(frozen=True, kw_only=True)
class FailureSignal:
    """Runtime 对一个失败的类型、重试边界和下一步建议。"""

    kind: FailureKind
    reason: str
    retryable: bool
    recovery_hint: str


@dataclass(frozen=True, kw_only=True)
class ExecutionBudget:
    """单次 Agent run 的步数、失败、时间和费用上限。"""

    max_steps: int = 12
    max_consecutive_failures: int = 3
    max_tool_repeats: int = 2
    timeout_seconds: float = 120.0
    cost_budget_usd: float | None = None


@dataclass(kw_only=True)
class StepController:
    """一次 Agent run 的重复检测、恢复分类和预算状态。

    ``AgentLoop`` 只编排 turn，``ToolExecutionPipeline`` 在 action 前后调用本对象。
    本对象只决定 repeat/retry/stop，不执行工具也不写 checkpoint。
    """

    budget: ExecutionBudget
    started_at: float = field(default_factory=time.time)
    tool_counts: dict[str, int] = field(default_factory=dict)
    failure_count: int = 0

    @classmethod
    def from_config(cls, config: RuntimeConfig) -> "StepController":
        """从 RuntimeConfig 构造带安全默认值的执行预算。

        预算字段在这里从配置收敛，避免主循环理解每个 limit。
        """

        return cls(
            budget=ExecutionBudget(
                max_steps=config.max_steps,
                max_consecutive_failures=config.max_consecutive_failures,
                max_tool_repeats=config.max_tool_repeats,
                timeout_seconds=config.timeout_seconds,
                cost_budget_usd=config.cost_budget_usd,
            )
        )

    def record_tool_intent(self, tool_call: ToolCall) -> FailureSignal | None:
        """按工具名和参数识别同一动作，超过阈值时返回停止信号。"""

        tool_call_identity = self._tool_key(tool_call)
        previous_call_count = self.tool_counts.get(tool_call_identity, 0)
        current_call_count = previous_call_count + 1
        self.tool_counts[tool_call_identity] = current_call_count
        if current_call_count > self.budget.max_tool_repeats:
            return FailureSignal(
                kind=FailureKind.REPEATED_ACTION,
                reason=f"repeated tool call: {tool_call.name}",
                retryable=False,
                recovery_hint="Stop the loop or choose a materially different action with new evidence.",
            )
        return None

    # 主要入口：把工具 Observation 分类为可恢复失败信号或正常结果。
    def classify_observation(self, observation: Observation) -> FailureSignal | None:
        """把原始 Observation 转换为重试判断和恢复建议。

        ``ToolExecutionPipeline`` 在每个工具结果后调用这里。返回的
        ``FailureSignal`` 进入 trace，并为下一轮提供 recovery hint；另外两个入口分别
        处理重复 intent 和预算耗尽。
        """

        if observation.success:
            self.failure_count = 0
            return None

        self.failure_count += 1
        normalized_observation_text = observation.content.lower()
        if (
            "unknown tool" in normalized_observation_text
            or "not allowed for this agent" in normalized_observation_text
        ):
            return FailureSignal(
                kind=FailureKind.UNKNOWN_TOOL,
                reason=observation.content,
                retryable=False,
                recovery_hint="Use only tools exposed in available_tools or stop with a clear limitation.",
            )
        if (
            "invalid arguments" in normalized_observation_text
            or "missing" in normalized_observation_text
        ):
            return FailureSignal(
                kind=FailureKind.INVALID_ARGUMENTS,
                reason=observation.content,
                retryable=True,
                recovery_hint="Repair the tool arguments using the tool schema and retry once.",
            )
        if (
            "old text not found" in normalized_observation_text
            or "old text is ambiguous" in normalized_observation_text
        ):
            return FailureSignal(
                kind=FailureKind.PATCH_MISMATCH,
                reason=observation.content,
                retryable=True,
                recovery_hint="Re-read the target file, choose a unique patch anchor, then retry.",
            )
        if any(
            policy_marker in normalized_observation_text
            for policy_marker in ("denied", "blocked", "needs_approval")
        ):
            return FailureSignal(
                kind=FailureKind.PERMISSION_DENIED,
                reason=observation.content,
                retryable=False,
                recovery_hint="Do not bypass policy; ask for approval or report the blocked action.",
            )
        if (
            "exit_code=" in normalized_observation_text
            and "exit_code=0" not in normalized_observation_text
        ):
            return FailureSignal(
                kind=FailureKind.COMMAND_FAILED,
                reason=observation.content,
                retryable=True,
                recovery_hint="Inspect the failure output, patch the root cause, and rerun the smallest validation.",
            )
        return FailureSignal(
            kind=FailureKind.TOOL_EXCEPTION,
            reason=observation.content,
            retryable=True,
            recovery_hint="Use the observation text as evidence, adjust the next action, and avoid repeating blindly.",
        )

    def should_stop(
        self,
        step: int,
        estimated_cost_usd: float = 0.0,
        *,
        include_step_limit: bool = True,
    ) -> FailureSignal | None:
        """检查累计预算；最终回答前可以跳过 step 上限。"""

        if include_step_limit and step >= self.budget.max_steps:
            return FailureSignal(
                kind=FailureKind.BUDGET_EXCEEDED,
                reason="max_steps reached",
                retryable=False,
                recovery_hint="Summarize current state and stop rather than continuing indefinitely.",
            )
        if self.failure_count >= self.budget.max_consecutive_failures:
            return FailureSignal(
                kind=FailureKind.BUDGET_EXCEEDED,
                reason="too many consecutive failed tools",
                retryable=False,
                recovery_hint="Stop and report the failure chain instead of looping.",
            )
        if time.time() - self.started_at > self.budget.timeout_seconds:
            return FailureSignal(
                kind=FailureKind.BUDGET_EXCEEDED,
                reason="timeout exceeded",
                retryable=False,
                recovery_hint="Stop and preserve enough state for a later resume.",
            )
        if (
            self.budget.cost_budget_usd is not None
            and estimated_cost_usd > self.budget.cost_budget_usd
        ):
            return FailureSignal(
                kind=FailureKind.BUDGET_EXCEEDED,
                reason="cost budget exceeded",
                retryable=False,
                recovery_hint="Stop before spending more model budget.",
            )
        return None

    def model_failure(self, error: dict) -> FailureSignal:
        """把无效或失败的模型响应归一化为恢复信号。

        Provider retry/fallback 属于 ModelGateway；tool recovery 属于工具执行管线，
        因此两类错误保持分离。
        """

        provider_error_code = str(
            error.get("code") or error.get("type") or "model_error"
        )
        provider_failure_is_retryable = provider_error_code in {
            "request_failed",
            "request_timeout",
            "rate_limited",
            "server_error",
            "temporary_failure",
            "timeout",
        }
        return FailureSignal(
            kind=FailureKind.MODEL_RESPONSE,
            reason=provider_error_code,
            retryable=provider_failure_is_retryable,
            recovery_hint="Retry through ModelGateway fallback if configured; otherwise stop with provider diagnostics.",
        )

    def _tool_key(self, tool_call: ToolCall) -> str:
        try:
            normalized_arguments = json.dumps(
                tool_call.arguments or {},
                ensure_ascii=False,
                sort_keys=True,
            )
        except TypeError:
            normalized_arguments = str(tool_call.arguments)
        return f"{tool_call.name}:{normalized_arguments}"

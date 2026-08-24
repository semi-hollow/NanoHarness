"""AgentLoop 的预算、重复动作检测和失败恢复分类。

可以把 ``StepController`` 理解为 Java 业务流程中的轻量运行控制器：它只返回
``FailureSignal`` 决策，不执行工具、不落 checkpoint，也不负责最终失败分类报告。
"""

import json
import time
from dataclasses import dataclass, field
from enum import Enum

from agent_forge.runtime.config import (
    DEFAULT_MAX_STEPS,
    DEFAULT_TIMEOUT_SECONDS,
    RuntimeConfig,
)
from agent_forge.runtime.domain.conversation import Observation, ToolCall


class FailureKind(Enum):
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    PERMISSION_DENIED = "permission_denied"
    PATCH_MISMATCH = "patch_mismatch"
    COMMAND_FAILED = "command_failed"
    VALIDATION_FAILED = "validation_failed"
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

    max_steps: int = DEFAULT_MAX_STEPS
    max_consecutive_failures: int = 3
    # 同一个工具和参数允许“首次尝试 + 一次重试”；第三次连续重复视为没有进展。
    max_consecutive_identical_tool_calls: int = 2
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    cost_budget_usd: float | None = None


@dataclass(kw_only=True)
class StepController:
    """一次 Agent run 的重复检测、恢复分类和预算状态。

    ``AgentLoop`` 只编排 Model Step，``ToolExecutionPipeline`` 在 action 前后调用本对象。
    本对象只决定 repeat/retry/stop，不执行工具也不写 checkpoint。
    """

    budget: ExecutionBudget
    started_at: float = field(default_factory=time.time)
    last_tool_call_key: str = ""
    consecutive_identical_tool_call_count: int = 0
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
                max_consecutive_identical_tool_calls=(
                    config.max_consecutive_identical_tool_calls
                ),
                timeout_seconds=config.timeout_seconds,
                cost_budget_usd=config.cost_budget_usd,
            )
        )

    def observe_tool_intent_for_repeat_limit(
        self,
        tool_call: ToolCall,
    ) -> FailureSignal | None:
        """识别连续相同 ToolCall；不同动作出现时立即重置计数。

        伪代码：生成稳定 ToolCall identity -> 相同则累加、不同则重置
        -> 超过连续阈值时返回 ``REPEATED_ACTION``。

        这里检查“模型是否仍在推进”，不是操作状态表的“状态变更操作是否已执行”。
        首次调用和一次同动作重试被允许；第三次连续给出相同工具和规范化参数时，返回
        ``REPEATED_ACTION``。调用方再按操作是否可能改变持久状态，决定跳过本次调用还是停止 run。
        """

        current_tool_call_key = self._stable_tool_call_key(tool_call)
        # 相同工具和规范化参数表示模型没有换动作；否则从新动作重新计数。
        if current_tool_call_key == self.last_tool_call_key:
            self.consecutive_identical_tool_call_count += 1
        else:
            self.last_tool_call_key = current_tool_call_key
            self.consecutive_identical_tool_call_count = 1

        # 计数只产生恢复信号；是否跳过或停止仍由 ToolExecutionPipeline 按 side effect 决定。
        if (
            self.consecutive_identical_tool_call_count
            > self.budget.max_consecutive_identical_tool_calls
        ):
            return FailureSignal(
                kind=FailureKind.REPEATED_ACTION,
                reason=(
                    "consecutive identical tool-call limit exceeded: "
                    f"{tool_call.name} "
                    f"({self.consecutive_identical_tool_call_count} > "
                    f"{self.budget.max_consecutive_identical_tool_calls})"
                ),
                retryable=False,
                recovery_hint=(
                    "Do not issue the same tool and arguments again without a different "
                    "action that produces new evidence."
                ),
            )
        return None

    # 主要入口：把工具 Observation 分类为可恢复失败信号或正常结果。
    def classify_observation(self, observation: Observation) -> FailureSignal | None:
        """把原始 Observation 转换为重试判断和恢复建议。

        伪代码：成功 -> 清零连续失败；验证命令已执行但目标未通过
        -> 可恢复 validation feedback；
        其余失败 -> 累加 failure_count -> 按工具、参数、Patch、权限、命令分类
        -> 无法细分时返回 ``TOOL_EXCEPTION`` fallback。

        ``ToolExecutionPipeline`` 在每个工具结果后调用这里。返回的
        ``FailureSignal`` 进入 trace，并为下一 Model Step 提供 recovery hint；另外两个入口分别
        处理重复 intent 和预算耗尽。
        """

        # region 1. 正常结果与业务验证失败：区分“工具坏了”和“测试发现问题”
        # 成功 Observation 证明当前工具链可用，立即清零连续工具失败计数。
        if observation.success:
            self.failure_count = 0
            return None

        # 验证工具已正常运行但测试/编译目标未通过，是下一 Model Step 要消费的业务反馈，
        # 不是工具网关、参数或执行环境故障。它会产生 recovery signal，但不会
        # 累加“连续工具故障”熔断计数；原地重复同一验证仍由 ToolCall 重复保护拦截。
        # execution_succeeded=True 表示验证命令正常完成；测试或编译目标未通过时应先修复
        # 对应问题，而不是把它误判为 Tool/环境不可用。
        if observation.execution_succeeded is True:
            self.failure_count = 0
            return FailureSignal(
                kind=FailureKind.VALIDATION_FAILED,
                reason=observation.content,
                retryable=True,
                recovery_hint=(
                    "Use the failing validation as evidence, patch the root cause, "
                    "then rerun the smallest relevant validation."
                ),
            )
        # endregion 1. 正常结果与业务验证失败结束

        # region 2. 工具故障分类：按最具体且可行动的恢复路径优先匹配
        self.failure_count += 1
        normalized_observation_text = observation.content.lower()
        # 未注册或本 Agent 不可见的工具无法靠相同参数重试恢复。
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
        # 参数缺失或物理格式错误可根据 schema 修正后重试一次。
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
        # Patch anchor 不存在或不唯一时必须重新读取目标，不能盲目重复 replace。
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
        # 权限与审批拒绝是治理结论，不允许模型用变形参数绕过后自动重试。
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
        # 命令确实启动但返回非零状态时，可依据 stderr/stdout 修复根因后再验证。
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
        # endregion 2. 工具故障分类结束

        # region 3. 未知失败 fallback：只表示无法细分，不宣称发生了 Python exception
        return FailureSignal(
            kind=FailureKind.TOOL_EXCEPTION,
            reason=observation.content,
            retryable=True,
            recovery_hint="Use the observation text as evidence, adjust the next action, and avoid repeating blindly.",
        )
        # endregion 3. 未知失败 fallback结束

    def should_stop(
        self,
        step: int,
        estimated_cost_usd: float = 0.0,
        *,
        include_step_limit: bool = True,
    ) -> FailureSignal | None:
        """依次检查 step、连续工具失败、运行时间和模型成本四项硬上限。

        伪代码：step -> consecutive failures -> wall clock -> cost；首个命中即停止。

        命中任一上限即返回不可重试的 ``BUDGET_EXCEEDED``；否则返回 ``None``。
        ``include_step_limit=False`` 只放开 step，供最后一次模型回答使用，其余预算仍生效。
        """

        # Step 上限最先检查；final-answer 调用只通过 include_step_limit 显式放开这一项。
        if include_step_limit and step >= self.budget.max_steps:
            return FailureSignal(
                kind=FailureKind.BUDGET_EXCEEDED,
                reason="max_steps reached",
                retryable=False,
                recovery_hint="Summarize current state and stop rather than continuing indefinitely.",
            )
        # 连续工具故障达到阈值说明当前恢复策略未推进，继续尝试只会形成失败循环。
        if self.failure_count >= self.budget.max_consecutive_failures:
            return FailureSignal(
                kind=FailureKind.BUDGET_EXCEEDED,
                reason=(
                    "too many consecutive failed tools: "
                    f"{self.failure_count} >= limit "
                    f"{self.budget.max_consecutive_failures}"
                ),
                retryable=False,
                recovery_hint="Stop and report the failure chain instead of looping.",
            )
        # Wall-clock 是 control-boundary deadline：每次回到本控制点都会收口，但当前实现
        # 不能 interrupt 已在进行中的 ModelPort/Tool request，因此请求可能越过预算后才停止。
        if time.time() - self.started_at > self.budget.timeout_seconds:
            return FailureSignal(
                kind=FailureKind.BUDGET_EXCEEDED,
                reason="timeout exceeded",
                retryable=False,
                recovery_hint="Stop and preserve enough state for a later resume.",
            )
        # Cost 只有配置上限时启用，且独立于 step/timeout 继续生效。
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

    def _stable_tool_call_key(self, tool_call: ToolCall) -> str:
        """用工具名和排序后的 JSON 参数生成稳定身份；provider call id 不参与。"""

        try:
            normalized_arguments = json.dumps(
                tool_call.arguments or {},
                ensure_ascii=False,
                sort_keys=True,
            )
        except TypeError:
            normalized_arguments = str(tool_call.arguments)
        return f"{tool_call.name}:{normalized_arguments}"

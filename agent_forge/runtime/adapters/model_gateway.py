"""Provider-neutral 模型调用、retry、fallback 与 usage Adapter。

系统角色：把一次 ``ModelPort.chat`` 收口成有界 primary attempts 和可选 fallback，
同时累计真实/估算 token、延迟与成本；不拥有 Context、Run deadline 或 Tool 执行。
输入：Prepared Model messages 与 visible tools；输出：最终 ``AgentResponse`` 和 usage。
相邻边界：HTTP client 只做一次 transport；AgentLoop 决定收到结果后的控制流。

折叠导航：1 retry policy；2 public call；3 retry/repair；4 usage estimation。
"""

import time
from dataclasses import dataclass

from agent_forge.contracts import ToolSchema
from agent_forge.runtime.adapters.openai_compatible import AgentResponse, LLMClient
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.domain.model import ModelCapabilities

from agent_forge.runtime.domain.model_usage import ModelUsage


# region 1. Retry policy：区分 transport retry、ToolCall repair 与 provider fallback
# 核心数据：模型重试、tool-call 修复和 fallback 的有界策略。
@dataclass(kw_only=True)
class RetryPolicy:
    """区分可重试、可修复和可切换 provider 的错误集合。"""

    max_attempts: int = 1
    backoff_seconds: float = 0.0
    retryable_error_codes: tuple[str, ...] = (
        "request_failed",
        "request_timeout",
        "rate_limited",
        "server_error",
        "invalid_json",
        "missing_choices",
        "missing_message",
        "empty_message",
    )
    repairable_error_codes: tuple[str, ...] = ("invalid_tool_call",)
    fallback_error_codes: tuple[str, ...] = (
        "request_failed",
        "request_timeout",
        "rate_limited",
        "server_error",
        "invalid_json",
        "missing_choices",
        "missing_message",
        "empty_message",
        "invalid_tool_call",
        "parse_failed",
    )
# endregion 1. Retry policy 结束


class ModelGateway(LLMClient):
    """把 Provider 差异收敛为一套有界尝试策略。

    主链只做三件事：调用 primary、在同一 Provider 内按错误类型重试、必要时切换
    fallback；Runtime 的 Turn 决策、窗口压缩和停止语义仍由 ``AgentLoop`` 拥有，
    当前 Gateway 不把 Run 剩余预算传播为 in-flight request cancellation。
    """

    def __init__(
        self,
        primary: LLMClient,
        provider: str = "deepseek",
        model: str = "deepseek-v4-flash",
        fallback: LLMClient | None = None,
        fallback_provider: str = "",
        fallback_model: str = "",
        retry_policy: RetryPolicy | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> None:
        self.primary = primary

        self.provider = provider
        self.model = model

        self.fallback = fallback
        self.fallback_provider = fallback_provider
        self.fallback_model = fallback_model

        self.retry_policy = retry_policy or RetryPolicy()
        self.capabilities = capabilities or ModelCapabilities()

        self.last_usage = ModelUsage(provider=provider, model=model)

    # region 1. Public call：primary 成功直接返回，否则按错误类型决定 fallback
    # 主要入口：调用主模型，并统一处理重试、协议修复、fallback 与 usage。
    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> AgentResponse:
        """执行一次与供应商无关的模型调用，并统一重试、回退和用量。"""

        # 1. Primary 是唯一默认路径；每次公开 chat 都重置本次调用的 usage 聚合。
        self.last_usage = ModelUsage(provider=self.provider, model=self.model)
        primary_response = self._call_with_retry(
            self.primary,
            self.last_usage,
            messages,
            tools,
        )
        if not primary_response.error:
            return primary_response

        # 2. 没有 fallback 或错误不适合换模型时，保留 primary 的真实失败交回 Runtime。
        if not self.fallback or not self._should_fallback(primary_response):
            return primary_response

        # 3. Fallback 使用独立 usage 容器执行，最后合并尝试次数、token 与成本证据。
        fallback_usage = ModelUsage(
            provider=self.fallback_provider, model=self.fallback_model
        )
        fallback_usage.fallback_used = True
        fallback_response = self._call_with_retry(
            self.fallback, fallback_usage, messages, tools
        )
        self.last_usage.fallback_used = True
        self.last_usage.fallback_provider = self.fallback_provider
        self.last_usage.fallback_model = self.fallback_model
        self.last_usage.merge(fallback_usage)
        return fallback_response

    def _should_fallback(self, model_response: AgentResponse) -> bool:
        """只对换模型可能改变结果的错误回退，窗口溢出交给 Runtime。"""

        response_error = model_response.error or {}
        error_code = str(response_error.get("code") or response_error.get("type") or "")
        return error_code in self.retry_policy.fallback_error_codes
    # endregion 1. Public call 结束

    # region 2. Provider 内有界尝试：transport retry 或一次格式 repair
    def _call_with_retry(
        self,
        model_client: LLMClient,
        model_usage: ModelUsage,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> AgentResponse:
        """在同一 Provider 上执行有界尝试，并累计每次调用的 Usage。

        成功立即返回；传输类错误复用原请求，确定可修复的 ToolCall 错误会追加纠错提示；
        其他错误或次数耗尽时返回最后一次响应。备用 Provider 由 ``chat`` 负责，本方法不切换。
        """

        max_attempts = max(1, self.retry_policy.max_attempts)
        latest_model_response = AgentResponse(
            content=None,
            tool_calls=[],
            error={"code": "not_called", "message": "model not called"},
        )
        attempt_messages = list(messages)
        # 每轮只允许三种出口：成功返回、次数/错误类型阻止继续、生成下一次有界请求。
        for attempt_index in range(max_attempts):
            # 1. 记录真实尝试/延迟/错误，以及 provider-reported 或估算的 usage/cost；
            # 不把重试隐藏成一次成功调用，也不把估算值冒充精确计费事实。
            call_started_at = time.time()
            latest_model_response = model_client.chat(attempt_messages, tools)
            latency_ms = int((time.time() - call_started_at) * 1000)
            model_usage.prompt_tokens_estimate += self._estimate_prompt_tokens(
                attempt_messages,
                tools,
            )
            model_usage.completion_tokens_estimate += self._estimate_completion_tokens(
                latest_model_response
            )
            model_usage.record_provider_usage(
                latest_model_response.usage,
                latest_model_response.response_id,
                latest_model_response.observed_model,
            )
            model_usage.estimated_cost_usd = self._estimate_cost_usd(model_usage)
            error_code = ""
            if latest_model_response.error:
                error_code = str(
                    latest_model_response.error.get("code")
                    or latest_model_response.error.get("type")
                    or "unknown"
                )
            model_usage.record_attempt(latency_ms, error_code)
            if not latest_model_response.error:
                return latest_model_response

            # 2. 已用完次数，或错误既不可传输重试也不可格式修复时，停止当前 Provider。
            if attempt_index >= max_attempts - 1:
                break
            retry_messages = self._retry_messages(
                latest_model_response,
                attempt_messages,
            )
            if retry_messages is None:
                break

            # 3. 传输错误复用原输入；ToolCall 格式错误才追加 repair system message。
            attempt_messages = retry_messages
            if self.retry_policy.backoff_seconds > 0:
                time.sleep(self.retry_policy.backoff_seconds)
        return latest_model_response

    def _retry_messages(
        self,
        model_response: AgentResponse,
        attempted_messages: list[Message],
    ) -> list[Message] | None:
        """区分 transport 重试、格式修复和必须交回 Runtime 的错误。"""

        response_error = model_response.error or {}
        error_code = str(response_error.get("code") or "")
        if error_code in self.retry_policy.repairable_error_codes:
            repair_prompt = str(response_error.get("repair_prompt") or "")
            if not repair_prompt:
                return None
            return [
                *attempted_messages,
                Message(role="system", content=repair_prompt),
            ]
        if error_code in self.retry_policy.retryable_error_codes:
            return list(attempted_messages)
        return None
    # endregion 2. Provider 内有界尝试结束

    # region 3. Usage estimation：provider 未返回精确值时提供明确标记的估算证据
    def _estimate_prompt_tokens(
        self, messages: list[Message], tools: list[ToolSchema]
    ) -> int:
        text_chars = sum(len(message.content or "") for message in messages)
        tool_chars = sum(len(str(tool)) for tool in tools)
        return max(1, (text_chars + tool_chars) // 4)

    def _estimate_completion_tokens(self, response: AgentResponse) -> int:
        if response.content:
            return max(1, len(response.content) // 4)
        return max(
            1,
            sum(
                len(call.name) + len(str(call.arguments))
                for call in response.tool_calls
            )
            // 4,
        )

    def _estimate_cost_usd(self, usage: ModelUsage) -> float:
        prices_per_million = {
            ("deepseek", "deepseek-v4-flash"): {
                "input_cache_hit": 0.0028,
                "input_cache_miss": 0.14,
                "output": 0.28,
            },
            ("deepseek", "deepseek-v4-pro"): {
                "input_cache_hit": 0.003625,
                "input_cache_miss": 0.435,
                "output": 0.87,
            },
            ("deepseek", "deepseek-chat"): {
                "input_cache_hit": 0.0028,
                "input_cache_miss": 0.14,
                "output": 0.28,
            },
            ("deepseek", "deepseek-reasoner"): {
                "input_cache_hit": 0.0028,
                "input_cache_miss": 0.14,
                "output": 0.28,
            },
            # OpenCode Go 按官网公布的“额度美元价值”计量；这里既用于成本证据，
            # 也用于 Runtime 的单 Case 熔断，不能让未知价格静默变成 0 成本。
            ("opencode-go", "glm-5.2"): {
                "input_cache_hit": 0.26,
                "input_cache_miss": 1.40,
                "output": 4.40,
            },
            ("opencode-go", "glm-5.1"): {
                "input_cache_hit": 0.26,
                "input_cache_miss": 1.40,
                "output": 4.40,
            },
            ("opencode-go", "kimi-k2.7-code"): {
                "input_cache_hit": 0.19,
                "input_cache_miss": 0.95,
                "output": 4.00,
            },
            ("opencode-go", "deepseek-v4-pro"): {
                "input_cache_hit": 0.003625,
                "input_cache_miss": 0.435,
                "output": 0.87,
            },
            ("opencode-go", "deepseek-v4-flash"): {
                "input_cache_hit": 0.0028,
                "input_cache_miss": 0.14,
                "output": 0.28,
            },
        }
        price = prices_per_million.get((usage.provider, usage.model))
        if not price:
            return 0.0

        prompt_tokens = usage.prompt_tokens or usage.prompt_tokens_estimate
        completion_tokens = usage.completion_tokens or usage.completion_tokens_estimate
        if usage.cache_hit_tokens or usage.cache_miss_tokens:
            input_cost = (
                usage.cache_hit_tokens / 1_000_000 * price["input_cache_hit"]
                + usage.cache_miss_tokens / 1_000_000 * price["input_cache_miss"]
            )
        else:
            input_cost = prompt_tokens / 1_000_000 * price["input_cache_miss"]
        output_cost = completion_tokens / 1_000_000 * price["output"]
        return round(input_cost + output_cost, 6)
    # endregion 3. Usage estimation 结束

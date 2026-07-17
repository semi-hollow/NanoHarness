import time
from dataclasses import dataclass

from agent_forge.runtime.llm_client import AgentResponse, LLMClient
from agent_forge.runtime.domain.conversation import Message

from .usage import ModelUsage


# 核心数据：模型重试、tool-call 修复和 fallback 的有界策略。
@dataclass
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


class ModelGateway(LLMClient):

    def __init__(
        self,
        primary: LLMClient,
        provider: str = "deepseek",
        model: str = "deepseek-v4-flash",
        fallback: LLMClient | None = None,
        fallback_provider: str = "",
        fallback_model: str = "",
        retry_policy: RetryPolicy | None = None,
    ) -> None:

        self.primary = primary

        self.provider = provider
        self.model = model

        self.fallback = fallback
        self.fallback_provider = fallback_provider
        self.fallback_model = fallback_model

        self.retry_policy = retry_policy or RetryPolicy()

        self.last_usage = ModelUsage(provider=provider, model=model)

    # 主要入口：调用主模型，并统一处理重试、协议修复、fallback 与 usage。
    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:
        """执行一次与供应商无关的模型调用，并统一重试、回退和用量。"""

        self.last_usage = ModelUsage(provider=self.provider, model=self.model)
        response = self._call_with_retry(self.primary, self.last_usage, messages, tools)
        if not response.error:
            return response

        if not self.fallback or not self._should_fallback(response):
            return response

        fallback_usage = ModelUsage(provider=self.fallback_provider, model=self.fallback_model)
        fallback_usage.fallback_used = True
        fallback_response = self._call_with_retry(self.fallback, fallback_usage, messages, tools)
        self.last_usage.fallback_used = True
        self.last_usage.fallback_provider = self.fallback_provider
        self.last_usage.fallback_model = self.fallback_model
        self.last_usage.merge(fallback_usage)
        return fallback_response

    def _should_fallback(self, response: AgentResponse) -> bool:
        """只对换模型可能改变结果的错误回退，窗口溢出交给 Runtime。"""

        error = response.error or {}
        code = str(error.get("code") or error.get("type") or "")
        return code in self.retry_policy.fallback_error_codes

    def _call_with_retry(
        self,
        client: LLMClient,
        usage: ModelUsage,
        messages: list[Message],
        tools: list[dict],
    ) -> AgentResponse:

        attempts = max(1, self.retry_policy.max_attempts)
        response = AgentResponse(None, [], {"code": "not_called", "message": "model not called"})
        attempt_messages = list(messages)
        for attempt in range(attempts):
            started = time.time()
            response = client.chat(attempt_messages, tools)
            latency_ms = int((time.time() - started) * 1000)
            usage.prompt_tokens_estimate += self._estimate_prompt_tokens(attempt_messages, tools)
            usage.completion_tokens_estimate += self._estimate_completion_tokens(response)
            usage.record_provider_usage(response.usage, response.response_id)
            usage.estimated_cost_usd = self._estimate_cost_usd(usage)
            error_code = ""
            if response.error:
                error_code = str(response.error.get("code") or response.error.get("type") or "unknown")
            usage.record_attempt(latency_ms, error_code)
            if not response.error:
                return response
            if attempt >= attempts - 1:
                break
            next_messages = self._retry_messages(response, attempt_messages)
            if next_messages is None:
                break
            attempt_messages = next_messages
            if self.retry_policy.backoff_seconds > 0:
                time.sleep(self.retry_policy.backoff_seconds)
        return response

    def _retry_messages(
        self,
        response: AgentResponse,
        messages: list[Message],
    ) -> list[Message] | None:
        """区分 transport 重试、格式修复和必须交回 Runtime 的错误。"""

        error = response.error or {}
        code = str(error.get("code") or "")
        if code in self.retry_policy.repairable_error_codes:
            repair_prompt = str(error.get("repair_prompt") or "")
            if not repair_prompt:
                return None
            return [*messages, Message("system", repair_prompt)]
        if code in self.retry_policy.retryable_error_codes:
            return list(messages)
        return None

    def _estimate_prompt_tokens(self, messages: list[Message], tools: list[dict]) -> int:

        text_chars = sum(len(message.content or "") for message in messages)
        tool_chars = sum(len(str(tool)) for tool in tools)
        return max(1, (text_chars + tool_chars) // 4)

    def _estimate_completion_tokens(self, response: AgentResponse) -> int:

        if response.content:
            return max(1, len(response.content) // 4)
        return max(1, sum(len(call.name) + len(str(call.arguments)) for call in response.tool_calls) // 4)

    def _estimate_cost_usd(self, usage: ModelUsage) -> float:

        prices_per_million = {
            ("deepseek", "deepseek-v4-flash"): {
                "input_cache_hit": 0.0028,
                "input_cache_miss": 0.14,
                "output": 0.28,
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

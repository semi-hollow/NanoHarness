import time
from dataclasses import dataclass

from agent_forge.runtime.llm_client import AgentResponse, LLMClient
from agent_forge.runtime.domain.conversation import Message

from .usage import ModelUsage


@dataclass
class RetryPolicy:

    max_attempts: int = 1
    backoff_seconds: float = 0.0


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

    # 主要入口：下方定义承接该模块的核心调用。
    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:
        """执行一次与供应商无关的模型调用，并统一重试、回退和用量。"""

        self.last_usage = ModelUsage(provider=self.provider, model=self.model)
        response = self._call_with_retry(self.primary, self.last_usage, messages, tools)
        if not response.error:
            return response

        if not self.fallback:
            return response

        fallback_usage = ModelUsage(provider=self.fallback_provider, model=self.fallback_model)
        fallback_usage.fallback_used = True
        fallback_response = self._call_with_retry(self.fallback, fallback_usage, messages, tools)
        self.last_usage.fallback_used = True
        self.last_usage.merge(fallback_usage)
        return fallback_response

    def _call_with_retry(
        self,
        client: LLMClient,
        usage: ModelUsage,
        messages: list[Message],
        tools: list[dict],
    ) -> AgentResponse:

        attempts = max(1, self.retry_policy.max_attempts)
        response = AgentResponse(None, [], {"code": "not_called", "message": "model not called"})
        for attempt in range(attempts):
            started = time.time()
            response = client.chat(messages, tools)
            latency_ms = int((time.time() - started) * 1000)
            usage.prompt_tokens_estimate += self._estimate_prompt_tokens(messages, tools)
            usage.completion_tokens_estimate += self._estimate_completion_tokens(response)
            usage.record_provider_usage(response.usage, response.response_id)
            usage.estimated_cost_usd = self._estimate_cost_usd(usage)
            error_code = ""
            if response.error:
                error_code = str(response.error.get("code") or response.error.get("type") or "unknown")
            usage.record_attempt(latency_ms, error_code)
            if not response.error:
                return response
            if attempt < attempts - 1 and self.retry_policy.backoff_seconds > 0:
                time.sleep(self.retry_policy.backoff_seconds)
        return response

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

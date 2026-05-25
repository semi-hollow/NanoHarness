import time
from dataclasses import dataclass

from agent_forge.runtime.llm_client import AgentResponse, LLMClient
from agent_forge.runtime.message import Message

from .usage import ModelUsage


@dataclass
class RetryPolicy:
    """Retry knobs for ModelGateway.

    This is intentionally small and deterministic. It proves the production
    concern without hiding behavior behind a third-party SDK: retry only happens
    when the normalized LLM response has ``error`` data.
    """

    max_attempts: int = 1
    backoff_seconds: float = 0.0


class ModelGateway(LLMClient):
    """Provider-agnostic LLM entry point used by agent runtimes.

    The gateway is the layer system reviewers expect in real systems: AgentLoop
    should not know whether it is calling Ollama, a company OpenAI-compatible
    endpoint, MiniMax, or a mock. It should receive a normalized AgentResponse
    plus usage telemetry.
    """

    def __init__(
        self,
        primary: LLMClient,
        provider: str = "mock",
        model: str = "mock",
        fallback: LLMClient | None = None,
        fallback_provider: str = "mock",
        fallback_model: str = "mock",
        retry_policy: RetryPolicy | None = None,
    ):
        """Wire primary/fallback clients without leaking provider details upward."""

        # The primary client can be mock, Ollama, company API, or any
        # OpenAI-compatible endpoint. AgentLoop only sees ModelGateway.chat().
        self.primary = primary

        # Provider/model are copied into ModelUsage so trace can answer:
        # "which model produced this action?"
        self.provider = provider
        self.model = model

        # Optional fallback is useful for offline demos and provider outages.
        # In production, fallback should be chosen by explicit policy, not hidden.
        self.fallback = fallback
        self.fallback_provider = fallback_provider
        self.fallback_model = fallback_model

        # Retry policy belongs here because it is provider-call behavior, not
        # tool recovery behavior.
        self.retry_policy = retry_policy or RetryPolicy()

        # Last logical call telemetry. AgentLoop writes this into trace after
        # every llm_call event.
        self.last_usage = ModelUsage(provider=provider, model=model)

    def chat(self, messages: list[Message], tools: list[dict]) -> AgentResponse:
        """Call the primary model with retry, then optional fallback."""

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
        """Run attempts and normalize retry bookkeeping."""

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
        """Estimate prompt tokens when provider usage metadata is unavailable."""

        text_chars = sum(len(message.content or "") for message in messages)
        tool_chars = sum(len(str(tool)) for tool in tools)
        return max(1, (text_chars + tool_chars) // 4)

    def _estimate_completion_tokens(self, response: AgentResponse) -> int:
        """Estimate output tokens from final text or normalized tool calls."""

        if response.content:
            return max(1, len(response.content) // 4)
        return max(1, sum(len(call.name) + len(str(call.arguments)) for call in response.tool_calls) // 4)

    def _estimate_cost_usd(self, usage: ModelUsage) -> float:
        """Estimate cost from provider/model pricing when the project knows it.

        Token counts are approximate because the standard-library client does
        not depend on provider SDK tokenizers. The pricing table should stay
        small and explicit: it is only for local budget awareness, not billing.
        DeepSeek prices come from the official Models & Pricing page. Cache-hit
        input is much cheaper than cache-miss input, so reporting both is useful
        for real cost conversations.
        """

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

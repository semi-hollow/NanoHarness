from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelUsage:

    provider: str
    model: str
    attempts: int = 0
    fallback_used: bool = False
    latency_ms: int = 0
    prompt_tokens_estimate: int = 0
    completion_tokens_estimate: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    reasoning_tokens: int = 0
    response_id: str = ""
    usage_source: str = "estimate"
    estimated_cost_usd: float = 0.0
    error_codes: list[str] = field(default_factory=list)
    raw_usage: dict[str, Any] = field(default_factory=dict)

    def record_attempt(self, latency_ms: int, error_code: str = "") -> None:

        self.attempts += 1
        self.latency_ms += latency_ms
        if error_code:
            self.error_codes.append(error_code)

    def record_provider_usage(self, usage: dict[str, Any] | None, response_id: str | None = None) -> None:

        if not usage:
            return
        self.usage_source = "provider"
        self.raw_usage = dict(usage)
        if response_id:
            self.response_id = response_id

        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or prompt + completion)
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}

        cache_hit = int(
            usage.get("prompt_cache_hit_tokens")
            or prompt_details.get("cached_tokens")
            or 0
        )
        cache_miss = int(usage.get("prompt_cache_miss_tokens") or 0)
        if prompt and cache_hit and not cache_miss:
            cache_miss = max(0, prompt - cache_hit)

        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total
        self.cache_hit_tokens += cache_hit
        self.cache_miss_tokens += cache_miss
        self.reasoning_tokens += int(completion_details.get("reasoning_tokens") or 0)

    def merge(self, other: "ModelUsage") -> None:

        self.attempts += other.attempts
        self.latency_ms += other.latency_ms
        self.prompt_tokens_estimate += other.prompt_tokens_estimate
        self.completion_tokens_estimate += other.completion_tokens_estimate
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.cache_hit_tokens += other.cache_hit_tokens
        self.cache_miss_tokens += other.cache_miss_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.estimated_cost_usd += other.estimated_cost_usd
        self.error_codes.extend(other.error_codes)
        if other.response_id:
            self.response_id = other.response_id
        if other.usage_source == "provider":
            self.usage_source = "provider"

    def to_dict(self) -> dict:

        return {
            "provider": self.provider,
            "model": self.model,
            "attempts": self.attempts,
            "fallback_used": self.fallback_used,
            "latency_ms": self.latency_ms,
            "prompt_tokens_estimate": self.prompt_tokens_estimate,
            "completion_tokens_estimate": self.completion_tokens_estimate,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "response_id": self.response_id,
            "usage_source": self.usage_source,
            "estimated_cost_usd": self.estimated_cost_usd,
            "error_codes": self.error_codes,
            "raw_usage": self.raw_usage,
        }

from dataclasses import dataclass, field


@dataclass
class ModelUsage:
    """Production-style telemetry for one logical model gateway call path.

    Real coding agents need this layer because provider calls fail, time out,
    and cost money. The project does not calculate real token prices yet, but
    it records the operational signals an interview-grade system must expose:
    attempts, fallback use, latency, and normalized error codes.
    """

    # Logical provider name: mock, openai-compatible, company gateway, Ollama, etc.
    provider: str

    # Concrete model id. Interview reason: model behavior/cost must be auditable.
    model: str

    # Number of provider attempts across retry/fallback. Needed for reliability.
    attempts: int = 0

    # True if primary failed and fallback answered. Helps debug degraded quality.
    fallback_used: bool = False

    # Cumulative latency for attempts. Needed for SLO/cost-effect tradeoffs.
    latency_ms: int = 0

    # Approximate input tokens when provider usage metadata is unavailable.
    prompt_tokens_estimate: int = 0

    # Approximate output tokens or tool-call payload tokens.
    completion_tokens_estimate: int = 0

    # Placeholder cost hook. Real systems fill it from ProviderProfile pricing.
    estimated_cost_usd: float = 0.0

    # Normalized provider/runtime error codes for badcase analysis.
    error_codes: list[str] = field(default_factory=list)

    def record_attempt(self, latency_ms: int, error_code: str = "") -> None:
        """Add one provider attempt to the usage summary."""

        self.attempts += 1
        self.latency_ms += latency_ms
        if error_code:
            self.error_codes.append(error_code)

    def to_dict(self) -> dict:
        """Return JSON-safe data for trace/session reports."""

        return {
            "provider": self.provider,
            "model": self.model,
            "attempts": self.attempts,
            "fallback_used": self.fallback_used,
            "latency_ms": self.latency_ms,
            "prompt_tokens_estimate": self.prompt_tokens_estimate,
            "completion_tokens_estimate": self.completion_tokens_estimate,
            "estimated_cost_usd": self.estimated_cost_usd,
            "error_codes": self.error_codes,
        }

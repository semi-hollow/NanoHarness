from dataclasses import dataclass, field


@dataclass
class ModelUsage:
    """Production-style telemetry for one logical model gateway call path.

    Real coding agents need this layer because provider calls fail, time out,
    and cost money. The project does not calculate real token prices yet, but
    it records the operational signals an interview-grade system must expose:
    attempts, fallback use, latency, and normalized error codes.
    """

    provider: str
    model: str
    attempts: int = 0
    fallback_used: bool = False
    latency_ms: int = 0
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
            "error_codes": self.error_codes,
        }

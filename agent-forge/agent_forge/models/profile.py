from dataclasses import dataclass, field


@dataclass
class ProviderProfile:
    """Model provider routing contract.

    Interviewers usually ask how a production agent switches between company
    models, OpenAI-compatible APIs, Ollama, and mock fallback. This profile is
    the explicit routing unit consumed by ModelGateway/CLI-level wiring.
    """

    # Human-readable profile key, for example "ollama-qwen" or "company-prod".
    name: str

    # Provider family. The runtime uses this for routing and reporting.
    provider: str

    # Concrete model name passed to the provider.
    model: str

    # OpenAI-compatible base URL; empty means use environment/defaults.
    base_url: str = ""

    # Per-request timeout. Agent-level timeout still lives in RuntimeConfig.
    timeout: int = 30

    # Lower priority wins when a policy chooses between multiple profiles.
    priority: int = 100

    # Free-form capabilities such as "coding", "cheap", "long-context".
    tags: set[str] = field(default_factory=set)


@dataclass
class GatewayPolicy:
    """Operational model policy for a run."""

    # How many provider attempts before the gateway gives up or falls back.
    retry_attempts: int = 2

    # Named fallback profile. Mock is safe for offline demos, not production.
    fallback_profile: str = "mock"

    # Latency SLO for provider calls. Current gateway records but does not route on it.
    max_latency_ms: int = 30_000

    # Budget hook for tool-heavy runs. RuntimeConfig has the active loop budget.
    max_tool_calls: int = 30

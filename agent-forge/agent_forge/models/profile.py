from dataclasses import dataclass, field


@dataclass
class ProviderProfile:
    """Model provider routing contract.

    Interviewers usually ask how a production agent switches between company
    models, OpenAI-compatible APIs, Ollama, and mock fallback. This profile is
    the explicit routing unit consumed by ModelGateway/CLI-level wiring.
    """

    name: str
    provider: str
    model: str
    base_url: str = ""
    timeout: int = 30
    priority: int = 100
    tags: set[str] = field(default_factory=set)


@dataclass
class GatewayPolicy:
    """Operational model policy for a run."""

    retry_attempts: int = 2
    fallback_profile: str = "mock"
    max_latency_ms: int = 30_000
    max_tool_calls: int = 30

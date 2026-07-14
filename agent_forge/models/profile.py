from dataclasses import dataclass, field


@dataclass
class ProviderProfile:

    name: str
    provider: str
    model: str
    base_url: str = ""
    timeout: int = 30
    priority: int = 100
    tags: set[str] = field(default_factory=set)


@dataclass
class GatewayPolicy:

    retry_attempts: int = 2
    fallback_profile: str = ""
    max_latency_ms: int = 30_000
    max_tool_calls: int = 30

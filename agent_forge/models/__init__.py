"""Model gateway package for provider routing, retry, fallback, and usage.

Why this package exists:
    AgentLoop should not care whether the backing model is MockLLM, DeepSeek,
    Ollama, a company gateway, or another OpenAI-compatible endpoint. This
    package normalizes provider calls into ``AgentResponse`` plus
    ``ModelUsage`` telemetry.

If removed:
    Provider-specific HTTP details, retry behavior, fallback behavior, and cost
    accounting would leak into the core loop.
"""

from .gateway import ModelGateway, RetryPolicy
from .profile import GatewayPolicy, ProviderProfile
from .usage import ModelUsage

__all__ = ["GatewayPolicy", "ModelGateway", "ProviderProfile", "RetryPolicy", "ModelUsage"]

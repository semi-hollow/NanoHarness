def risk_registry():
    """Return known production risks and the project modules that illustrate them."""

    return {
        "tool_execution": "permission + sandbox + command policy",
        "model_variance": "MockLLM for tests; OpenAI-compatible path for integration",
        "context_overflow": "context budget report",
        "false_success_claim": "output guardrail",
    }

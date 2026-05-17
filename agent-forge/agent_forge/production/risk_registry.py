def risk_registry():
    """Return known production risks and the project modules that illustrate them."""

    return {
        "tool_execution": "permission + sandbox + command policy",
        "model_variance": "ModelGateway retry/fallback + OpenAI-compatible path",
        "context_overflow": "context budget report",
        "false_success_claim": "output guardrail",
        "multi_agent_conflict": "OwnershipPlan + conflict-aware TaskScheduler batches",
        "unsafe_code_change": "DiffTracker + rollback bundle + run report",
        "weak_validation": "DiagnosticsTool + unittest trace + eval history",
    }

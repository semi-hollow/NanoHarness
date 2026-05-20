def readiness_checklist():
    """Return production-readiness topics this learning project documents.

    This function is not runtime logic. It is a compact map of "what production
    concerns does this project demonstrate?" for interview preparation.
    """

    return [
        "model gateway and fallback",
        "provider profile and token/cost telemetry",
        "runtime-backed multi-agent workers",
        "conflict-aware task graph scheduling",
        "file ownership planning",
        "artifact contract between workers",
        "session and run artifact storage",
        "diff tracking and rollback bundle",
        "diagnostics tool",
        "container sandbox",
        "audit log retention",
        "eval history",
    ]

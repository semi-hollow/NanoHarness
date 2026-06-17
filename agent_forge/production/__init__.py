"""Run artifact and public-readiness helpers.

Why this package exists:
    The runtime modifies code, so each run needs evidence around what changed,
    how to inspect it, and how to roll it back. These modules are intentionally
    separate from ``runtime`` so AgentLoop can focus on behavior while this
    package focuses on persisted artifacts.

Read first:
    ``diff_tracker.py`` captures before/after code changes and rollback data.
    ``run_report.py`` writes the session-level markdown summary.
    ``ownership.py`` and ``risk_registry.py`` support multi-agent/review
    explanations but are not the main execution path.

If removed:
    Runs would still execute, but you would lose the artifact layer needed for
    audit, rollback, and project-readiness review.
"""

from .readiness import readiness_checklist

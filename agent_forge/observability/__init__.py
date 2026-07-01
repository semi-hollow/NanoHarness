"""Trace, metrics, evidence, and usage reporting package.

Why this package exists:
    A useful CodingAgent must be debuggable after the run. Final answers are
    not enough; we need step-level context, model, tool, permission, recovery,
    token, latency, and cost evidence.

Read first:
    ``trace.py`` records the raw event stream.
    ``usage_report.py`` derives the human-readable token/context/tool report.
    ``metrics.py`` summarizes success/failure counts.
    ``evidence.py`` records observation citations for final answers.

If removed:
    You could see whether a run ended, but not why it made each decision or
    where context/cost/tool failures happened.
"""

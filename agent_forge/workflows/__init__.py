"""Deterministic workflow and review-gate package.

Why this package exists:
    Not every problem should be solved by an autonomous loop. This package
    keeps fixed control-flow baselines and gates next to the agent runtime so
    reviewers can compare rule-driven execution with ReAct execution.

Read first:
    ``coding_workflow.py`` is the small deterministic baseline.
    ``task_graph.py`` shows dependency-aware task scheduling.
    ``review_workflow.py`` is the deterministic diff review gate.

If removed:
    The project would only demonstrate autonomous behavior and could not answer
    when a workflow or deterministic reviewer is safer than an agent loop.
"""

from .coding_workflow import run_workflow

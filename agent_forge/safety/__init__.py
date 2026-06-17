"""Deterministic safety and policy package.

Why this package exists:
    Prompt instructions are not enforcement. This package contains the hard
    checks that run before or around tool execution: path sandboxing, command
    allowlists, permission decisions, and input/output/tool guardrails.

Read first:
    ``permission.py`` explains allow/ask/deny.
    ``command_policy.py`` explains which shell commands are allowed.
    ``sandbox.py`` keeps file paths inside the workspace.
    ``guardrails.py`` catches task/output/tool-level policy failures.

If removed:
    Risky actions such as external path reads, network commands, destructive
    shell commands, or false validation claims would depend on the model
    choosing to behave well.
"""

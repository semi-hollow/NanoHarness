"""Runtime control-plane package.

Why this package exists:
    ``runtime`` owns the live execution loop and every control concern that
    keeps an LLM from becoming an unbounded script runner: step budgets,
    repeated-action detection, execution environment, hooks, task checkpoints,
    provider messages, and stop reasons.

Read first:
    ``agent_loop.py`` is the main ReAct loop.
    ``control.py`` owns failure classification and budgets.
    ``hooks.py`` owns pre/post tool policies.
    ``execution_environment.py`` owns local/worktree/OCI boundaries.
    ``task_state.py`` owns checkpoint/resume/replay.

If removed:
    The project would lose its core value: converting model output into a
    controlled, auditable, recoverable code-execution process.

Keep this module free of ``AgentLoop`` imports. Several low-level modules import
``agent_forge.runtime.observation``; importing ``AgentLoop`` from here would pull
context/memory back in and create a circular import during IDE indexing or
single-module tests.
"""

from .config import RuntimeConfig
from .control import ExecutionBudget, FailureKind, FailureSignal, StepController

__all__ = [
    "ExecutionBudget",
    "FailureKind",
    "FailureSignal",
    "RuntimeConfig",
    "StepController",
]

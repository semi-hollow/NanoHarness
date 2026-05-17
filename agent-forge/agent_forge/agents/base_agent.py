from dataclasses import dataclass


@dataclass
class AgentResult:
    """Common return value for demo subagents.

    This is intentionally smaller than a production agent result. A real worker
    would usually return structured artifacts such as patch sets, risk level,
    files touched, token usage, retry hints, and confidence.
    """

    name: str
    output: str


class BaseAgent:
    """Base interface used by Planner/Coding/Tester/Reviewer role objects.

    These role objects are not full autonomous agents. They share one mutable
    supervisor state dictionary and perform one narrow step each. The interface
    exists so the multi-agent demo can show orchestration mechanics without
    introducing a second runtime framework beside AgentLoop.
    """

    name = "BaseAgent"

    def run(self, state: dict) -> AgentResult:
        """Read and update shared supervisor state, then return an output."""

        raise NotImplementedError

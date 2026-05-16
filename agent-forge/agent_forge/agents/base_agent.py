from dataclasses import dataclass


@dataclass
class AgentResult:
    """Common return value for demo subagents."""

    name: str
    output: str


class BaseAgent:
    """Base interface used by Planner/Coding/Tester/Reviewer agents."""

    name = "BaseAgent"

    def run(self, state: dict) -> AgentResult:
        """Read and update shared supervisor state, then return an output."""

        raise NotImplementedError

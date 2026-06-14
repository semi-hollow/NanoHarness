from agent_forge.runtime.observation import Observation

from .base import Tool


class AskHumanTool(Tool):
    """Represent a human approval step in deterministic demos."""

    name = "ask_human"
    description = "approval simulation"

    def __init__(self, auto: bool = True):
        """Use `auto` to simulate approve/reject without blocking terminal input."""

        self.auto = auto

    def schema(self):
        """Tell the LLM it can ask one question when it needs approval."""

        return {"name": self.name, "description": self.description, "arguments": {"question": "str"}}

    def execute(self, arguments):
        """Return a synthetic approval Observation for traceability."""

        return Observation(self.name, self.auto, "approved" if self.auto else "needs_approval")

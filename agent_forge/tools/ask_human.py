from agent_forge.runtime.observation import Observation

from .base import Tool


class AskHumanTool(Tool):
    """Represent a synthetic human checkpoint for controlled agent runs.

    Real side-effect approval is handled by ``ApprovalStore`` and
    ``forge approve``. This tool exists for low-risk clarification traces and
    mini-case scenarios where blocking terminal input would make tests and
    demos brittle.
    """

    name = "ask_human"
    description = "synthetic human checkpoint"

    def __init__(self, auto: bool = True):
        """Use `auto` to return approve/reject without blocking terminal input."""

        self.auto = auto

    def schema(self):
        """Tell the LLM it can ask one question when it needs approval."""

        return {"name": self.name, "description": self.description, "arguments": {"question": "str"}}

    def execute(self, arguments):
        """Return a synthetic checkpoint Observation for traceability."""

        return Observation(self.name, self.auto, "approved" if self.auto else "needs_approval")

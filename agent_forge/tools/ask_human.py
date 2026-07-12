from agent_forge.runtime.observation import Observation

from .base import Tool


class AskHumanTool(Tool):
    """Declare a human question that AgentLoop persists before pausing."""

    name = "ask_human"
    description = "request durable human input; the run pauses until forge respond and resume"

    def schema(self):
        """Tell the LLM it can persist one clarification request."""

        return {
            "name": self.name,
            "description": self.description,
            "arguments": {"question": "str", "choices": "list"},
            "required": ["question"],
        }

    def execute(self, arguments):
        """Fail closed when called outside AgentLoop's control-plane interception."""

        return Observation(
            self.name,
            False,
            "human input control signal must be persisted and handled by AgentLoop",
        )

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation

from .base import Tool


class AskHumanTool(Tool):
    """声明由 ToolExecutionPipeline 转交 RunLifecycle 的人工问题。"""

    name = "ask_human"
    description = "request durable human input; the run pauses until forge respond and resume"

    def schema(self) -> ToolSchema:

        return {
            "name": self.name,
            "description": self.description,
            "arguments": {"question": "str", "choices": "list"},
            "required": ["question"],
        }

    def execute(self, arguments: ToolArguments) -> Observation:
        """脱离工具执行管线直接调用时 fail closed。"""

        return Observation(
            self.name,
            False,
            "human input control signal must be persisted and handled by AgentLoop",
        )

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.permission import PermissionDecision, PermissionPolicy
from agent_forge.safety.sandbox import WorkspaceSandbox

from .base import Tool


class WriteFileTool(Tool):

    name = "write_file"
    description = "write file"

    def __init__(self, sandbox: WorkspaceSandbox, auto_approve_writes: bool = True) -> None:

        self.sandbox = sandbox
        self.policy = PermissionPolicy(auto_approve_writes)
        self.auto_approve_writes = auto_approve_writes

    def schema(self) -> ToolSchema:

        return {
            "name": self.name,
            "description": self.description,
            "arguments": {"path": "str", "content": "str"},
        }

    def execute(self, arguments: ToolArguments) -> Observation:

        decision, reason = self.policy.decide("write")
        if decision == PermissionDecision.DENY:
            return Observation(self.name, False, reason)
        if decision == PermissionDecision.ASK and not self.auto_approve_writes:
            return Observation(self.name, False, "needs_approval")

        path = self.sandbox.ensure_safe_path(arguments["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments["content"], encoding="utf-8")
        return Observation(self.name, True, f"written: {arguments['path']}")

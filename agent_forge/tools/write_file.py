from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.permission import PermissionDecision, PermissionPolicy
from agent_forge.safety.sandbox import WorkspaceSandbox

from .base import Tool


class WriteFileTool(Tool):
    """Write a full file when the permission policy allows it.

    `apply_patch` is preferred for small repairs, but full-file writes are still
    useful for generated config or small files when policy allows them.
    """

    name = "write_file"
    description = "write file"

    def __init__(self, sandbox: WorkspaceSandbox, auto_approve_writes: bool = True) -> None:
        """Store sandbox and write policy used before touching disk."""

        self.sandbox = sandbox
        self.policy = PermissionPolicy(auto_approve_writes)
        self.auto_approve_writes = auto_approve_writes

    def schema(self) -> ToolSchema:
        """Tell the LLM this tool needs a target path and complete content."""

        return {
            "name": self.name,
            "description": self.description,
            "arguments": {"path": "str", "content": "str"},
        }

    def execute(self, arguments: ToolArguments) -> Observation:
        """Write content after approval and workspace path validation."""

        decision, reason = self.policy.decide("write")
        if decision == PermissionDecision.DENY:
            return Observation(self.name, False, reason)
        if decision == PermissionDecision.ASK and not self.auto_approve_writes:
            return Observation(self.name, False, "needs_approval")

        path = self.sandbox.ensure_safe_path(arguments["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments["content"], encoding="utf-8")
        return Observation(self.name, True, f"written: {arguments['path']}")

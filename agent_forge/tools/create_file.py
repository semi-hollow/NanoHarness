"""只创建新文件的受治理写工具。"""

from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.permission import PermissionDecision, PermissionPolicy
from agent_forge.safety.sandbox import WorkspaceSandbox

from .base import Tool


class CreateFileTool(Tool):
    """在 workspace 内创建不存在的文件，拒绝覆盖已有内容。

    ``write_file`` 能覆盖整个文件，能力过宽；``replace_text`` 又无法创建新文件。
    本工具补齐两者之间的最小能力缺口，同时保留审批和路径沙箱。
    """

    name = "create_file"
    description = (
        "Create one new workspace file. The target must not already exist; "
        "use replace_text to modify an existing file."
    )

    def __init__(
        self,
        sandbox: WorkspaceSandbox,
        auto_approve_writes: bool = True,
    ) -> None:
        self.sandbox = sandbox
        self.policy = PermissionPolicy(auto_approve_writes)
        self.auto_approve_writes = auto_approve_writes

    def schema(self) -> ToolSchema:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": {"path": "str", "content": "str"},
        }

    # 主要入口：审批通过后，仅在目标不存在时完成一次原子语义的创建。
    def execute(self, arguments: ToolArguments) -> Observation:
        decision, reason = self.policy.decide("write")
        if decision == PermissionDecision.DENY:
            return Observation(
                tool_name=self.name,
                success=False,
                content=reason,
            )
        if decision == PermissionDecision.ASK and not self.auto_approve_writes:
            return Observation(
                tool_name=self.name,
                success=False,
                content="needs_approval",
            )

        target_path = self.sandbox.ensure_safe_path(arguments["path"])
        if target_path.exists():
            return Observation(
                tool_name=self.name,
                success=False,
                content=(
                    f"create target already exists: {arguments['path']}; "
                    "use replace_text instead"
                ),
            )

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(arguments["content"], encoding="utf-8")
        return Observation(
            tool_name=self.name,
            success=True,
            content=f"created new file: {arguments['path']}",
        )

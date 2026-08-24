"""只创建新文件的受治理写工具。"""

from agent_forge.context.adapters.repository_map import invalidate_repo_map
from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.permission import PermissionDecision, PermissionPolicy
from agent_forge.safety.sandbox import WorkspaceSandbox

from agent_forge.tools.base import Tool


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

    # 主要入口：基础权限通过后，仅为检查时不存在的目标创建新文件。
    def execute(self, arguments: ToolArguments) -> Observation:
        """基础权限 -> 安全且不存在的目标 -> 创建正文并返回 Observation。

        这是单次创建流程，不宣称跨进程原子性；若需要并发安全，必须由更外层协调。
        """

        # 1. 主链先由 ToolAuthorizationGate 解析 ASK；这里保留 direct-call 防御。
        # 未自动放行时只返回 needs_approval Observation，不自行创建或伪造人工审批事实。
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

        # 2. Sandbox 限制 workspace 边界；已有目标必须由 replace_text 修改，
        # 防止 create 意图静默退化成整文件覆盖。
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

        # 3. 只为明确的新目标补齐父目录并写入正文，随后返回可审计结果。
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(arguments["content"], encoding="utf-8")
        invalidate_repo_map(self.sandbox.workspace_root)
        return Observation(
            tool_name=self.name,
            success=True,
            content=f"created new file: {arguments['path']}",
        )

"""通用整文件覆盖工具；能力宽于锚点替换，因此由 Router 标为高风险写能力。"""

from agent_forge.context.adapters.repository_map import invalidate_repo_map
from agent_forge.contracts import ToolArguments, ToolSchema
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.safety.permission import PermissionDecision, PermissionPolicy
from agent_forge.safety.sandbox import WorkspaceSandbox

from agent_forge.tools.base import Tool


class WriteFileTool(Tool):
    """在 workspace 内写入完整文件内容。

    这是普通修复任务的宽能力 fallback；SWE-bench 主链会隐藏它，优先使用
    ``replace_text`` 或 ``create_file`` 保持改动边界可解释。
    """

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

    # 主要入口：基础权限通过后，在沙箱内完成一次整文件写入。
    def execute(self, arguments: ToolArguments) -> Observation:
        """校验基础写策略与路径边界，再覆盖目标文件并返回 Observation。"""

        # 1. 主链先由 ToolAuthorizationGate 解析 ASK；这里保留 direct-call 防御。
        # 未自动放行时只返回 needs_approval Observation，不自行创建或伪造人工审批事实。
        decision, reason = self.policy.decide("write")
        if decision == PermissionDecision.DENY:
            return Observation(self.name, False, reason)
        if decision == PermissionDecision.ASK and not self.auto_approve_writes:
            return Observation(self.name, False, "needs_approval")

        # 2. Sandbox 把目标限制在 workspace；父目录只为这次明确目标创建。
        path = self.sandbox.ensure_safe_path(arguments["path"])
        target_existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)

        # 3. 本工具按契约覆盖完整正文；结果作为 Tool Observation 返回主循环。
        path.write_text(arguments["content"], encoding="utf-8")
        if not target_existed:
            invalidate_repo_map(self.sandbox.workspace_root)
        return Observation(self.name, True, f"written: {arguments['path']}")

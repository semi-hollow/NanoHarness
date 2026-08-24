"""Runtime Tool action 的第一层确定性权限分类。

系统角色：把 read/write/command/memory/coordination 等动作映射为 ALLOW/ASK/DENY；
它不执行 Approval、不验证 provenance，也不调用 Tool。
输入：action + optional command；输出：基础 decision/reason。
相邻边界：ToolAuthorizationGate 合并 Hook/Approval；CommandPolicy 细分命令 allowlist；
专项 Runtime 规则继续验证 Memory/LIVE 等业务边界。
"""

from enum import Enum
from .command_policy import check_command


class PermissionDecision(Enum):
    ALLOW = "allow"

    ASK = "ask"

    DENY = "deny"


class PermissionPolicy:
    """把动作映射为基础 ALLOW/ASK/DENY，不执行审批或工具。

    ``ASK`` 只要求后续 ``ToolAuthorizationGate`` 解析自动放行或人工授权事实；内置写
    Tool 另保留 direct-call 防御检查。``ALLOW`` 也不绕过 ``remember_memory`` provenance、
    ``coordination_publish`` route/version、Sandbox 等专项边界。
    """

    def __init__(self, auto_approve_writes: bool = True) -> None:
        self.auto_approve_writes = auto_approve_writes

    # 策略入口：把动作映射为基础决策；命令文本再交给 CommandPolicy。
    def decide(self, action: str, command: str = "") -> tuple[PermissionDecision, str]:
        """返回确定性基础决策和可写入 trace 的原因。"""

        # 读取、受限验证和两类 Runtime 专项动作先通过基础分类；专项动作的 provenance、
        # route/event/version 等业务校验仍由各自执行管线 fail closed。
        if action in {"read", "list", "search"}:
            return PermissionDecision.ALLOW, "read/list/search allowed"
        if action == "validate":
            return PermissionDecision.ALLOW, "bounded validation allowed"
        if action == "memory_write":
            return PermissionDecision.ALLOW, "explicit user memory provenance required"
        if action == "coordination_publish":
            return PermissionDecision.ALLOW, "Runtime-authorized coordination allowed"

        # 普通写入只产生 ASK 分类，不能把它解释成已有人批准或已经写入成功。
        if action == "write":
            return PermissionDecision.ASK, "write needs approval"

        # 命令只在窄 allowlist 内直接通过；CommandPolicy 不负责启动进程。
        if action == "run_command":
            ok, reason = check_command(command)
            return (PermissionDecision.ALLOW if ok else PermissionDecision.DENY), reason

        # 明确高风险动作和未知动作都默认拒绝，避免新增 action 意外获得权限。
        if action in {"network", "delete", "external_directory"}:
            return PermissionDecision.DENY, f"{action} denied"
        return PermissionDecision.DENY, "unsupported action"

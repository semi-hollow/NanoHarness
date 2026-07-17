from enum import Enum
from .command_policy import check_command


class PermissionDecision(Enum):

    ALLOW = "allow"

    ASK = "ask"

    DENY = "deny"


class PermissionPolicy:

    def __init__(self, auto_approve_writes: bool = True) -> None:

        self.auto_approve_writes = auto_approve_writes

    # 运行时端口：把动作映射为 allow/ask/deny；命令再交给 CommandPolicy。
    def decide(self, action: str, command: str = "") -> tuple[PermissionDecision, str]:
        """返回确定性权限决策和可写入 trace 的原因。"""

        if action in {"read", "list", "grep"}:
            return PermissionDecision.ALLOW, "read/list/grep allowed"
        if action in {"write", "apply_patch"}:

            return PermissionDecision.ASK, "write needs approval"
        if action == "run_command":
            ok, reason = check_command(command)
            return (PermissionDecision.ALLOW if ok else PermissionDecision.DENY), reason
        if action in {"network", "delete", "external_directory"}:
            return PermissionDecision.DENY, f"{action} denied"
        return PermissionDecision.DENY, "unsupported action"

from enum import Enum
from .command_policy import check_command


class PermissionDecision(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionPolicy:
    def __init__(self, auto_approve_writes: bool = True):
        self.auto_approve_writes = auto_approve_writes

    def decide(self, action: str, command: str = "") -> tuple[PermissionDecision, str]:
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

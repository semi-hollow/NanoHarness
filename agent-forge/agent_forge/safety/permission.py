from enum import Enum
from .command_policy import check_command


class PermissionDecision(Enum):
    """Runtime decision used before a tool can touch the workspace."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionPolicy:
    """Central policy for read/write/command decisions."""

    def __init__(self, auto_approve_writes: bool = True):
        """Store whether demo write actions should be auto-approved."""

        self.auto_approve_writes = auto_approve_writes

    def decide(self, action: str, command: str = "") -> tuple[PermissionDecision, str]:
        """Return allow/ask/deny plus a reason for trace and debugging."""

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

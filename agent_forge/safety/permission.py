from enum import Enum
from .command_policy import check_command


class PermissionDecision(Enum):
    """Runtime decision used before a tool can touch the workspace."""

    # Safe action can run immediately.
    ALLOW = "allow"

    # Human approval required. Local runs can auto-approve, but trace still
    # records the approval boundary.
    ASK = "ask"

    # Runtime must not execute the action.
    DENY = "deny"


class PermissionPolicy:
    """Central policy for read/write/command decisions.

    Prompt instructions should not decide whether a tool is allowed. This policy
    is the deterministic gate used after the model proposes an action.
    """

    def __init__(self, auto_approve_writes: bool = True) -> None:
        """Store whether write actions should be auto-approved locally."""

        self.auto_approve_writes = auto_approve_writes

    def decide(self, action: str, command: str = "") -> tuple[PermissionDecision, str]:
        """Return allow/ask/deny plus a reason for trace and debugging."""

        if action in {"read", "list", "grep"}:
            return PermissionDecision.ALLOW, "read/list/grep allowed"
        if action in {"write", "apply_patch"}:
            # Writes are ASK even when auto approval is enabled; AgentLoop logs
            # the approval event so audit shows that the action was high impact.
            return PermissionDecision.ASK, "write needs approval"
        if action == "run_command":
            ok, reason = check_command(command)
            return (PermissionDecision.ALLOW if ok else PermissionDecision.DENY), reason
        if action in {"network", "delete", "external_directory"}:
            return PermissionDecision.DENY, f"{action} denied"
        return PermissionDecision.DENY, "unsupported action"

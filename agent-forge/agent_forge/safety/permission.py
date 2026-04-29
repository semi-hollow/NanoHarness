from enum import Enum
from .command_policy import check_command
class PermissionDecision(Enum): ALLOW="allow"; ASK="ask"; DENY="deny"

def decide(action:str, auto_approve_writes=True, command:str=""):
    if action in {"list","read","grep"}: return PermissionDecision.ALLOW,"read allowed"
    if action in {"write","apply_patch"}: return (PermissionDecision.ALLOW if auto_approve_writes else PermissionDecision.ASK),"write requires approval"
    if action=="run_command":
        ok,reason=check_command(command)
        return (PermissionDecision.ALLOW if ok else PermissionDecision.DENY),reason
    return PermissionDecision.DENY,"unsupported action"

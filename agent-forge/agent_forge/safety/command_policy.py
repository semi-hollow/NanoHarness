import shlex

ALLOW = [
    ["python", "-m", "unittest"],
    ["python", "-m", "unittest", "discover"],
    ["git", "status"],
    ["git", "diff"],
]
DENY_PREFIX = ["rm", "del", "rmdir", "curl", "wget", "ssh", "scp", "chmod", "chown", "format", "mkfs", "powershell"]
DENY_EXACT = ["git push", "git reset --hard", "powershell Remove-Item", "rm -rf"]


def check_command(command: str) -> tuple[bool, str]:
    if not command.strip():
        return False, "empty command"
    lowered = command.strip().lower()
    if lowered in [x.lower() for x in DENY_EXACT]:
        return False, "dangerous command blocked"
    parts = shlex.split(command)
    if not parts:
        return False, "empty command"
    if parts[0].lower() in DENY_PREFIX:
        return False, "dangerous command blocked"
    for allowed in ALLOW:
        if parts[: len(allowed)] == allowed:
            return True, "allow"
    return False, "not allowlisted"

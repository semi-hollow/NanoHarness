import shlex

PYTHON_COMMANDS = {"python", "python3", "python3.11"}

DENY_PREFIX = {
    "rm", "del", "rmdir", "curl", "wget", "ssh", "scp",
    "chmod", "chown", "format", "mkfs", "powershell"
}

DENY_EXACT = {
    "git push",
    "git reset --hard",
    "powershell remove-item",
    "rm -rf",
}


def check_command(command: str) -> tuple[bool, str]:
    if not command.strip():
        return False, "empty command"

    lowered = command.strip().lower()
    if lowered in DENY_EXACT:
        return False, "dangerous command blocked"

    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return False, f"invalid command: {exc}"

    if not parts:
        return False, "empty command"

    first = parts[0].lower()
    if first in DENY_PREFIX:
        return False, "dangerous command blocked"

    if first in PYTHON_COMMANDS and len(parts) >= 3 and parts[1:3] == ["-m", "unittest"]:
        return True, "allow unittest"

    if parts[:2] == ["git", "status"]:
        return True, "allow git status"

    if parts[:2] == ["git", "diff"]:
        return True, "allow git diff"

    return False, "not allowlisted"

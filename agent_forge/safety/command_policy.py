import shlex

PYTHON_COMMANDS = {"python", "python3", "python3.11"}

# Prefix blocklist for commands that are too risky for a local coding-agent run.
# The final gate is still the allowlist below.
DENY_PREFIX = {
    "rm", "del", "rmdir", "curl", "wget", "ssh", "scp",
    "chmod", "chown", "format", "mkfs", "powershell", "sudo",
    "shutdown", "reboot"
}

DENY_EXACT = {
    "git push",
    "git reset --hard",
    "powershell remove-item",
    "rm -rf",
}


def check_command(command: str) -> tuple[bool, str]:
    """Allow safe validation commands and read-only git inspection.

    Command execution is one of the highest-risk tools. This project uses an
    allowlist because it is easier to reason about in technical walkthroughs:
    common Python test commands and read-only git commands are allowed; network,
    deletion, privilege, and push commands are blocked.
    """

    if not command.strip():
        return False, "empty command"

    lowered = command.strip().lower()
    if lowered in DENY_EXACT:
        return False, "dangerous command blocked"

    try:
        # shlex avoids shell=True parsing and lets us reason about the actual
        # executable requested by the model.
        parts = shlex.split(command)
    except ValueError as exc:
        return False, f"invalid command: {exc}"

    if not parts:
        return False, "empty command"

    first = parts[0].lower()
    if first in DENY_PREFIX:
        return False, "dangerous command blocked"

    if first in PYTHON_COMMANDS and len(parts) >= 3:
        module = parts[2]
        if parts[1] == "-m" and module in {"unittest", "pytest", "compileall"}:
            return True, f"allow python -m {module}"

    if first == "pytest":
        return True, "allow pytest"

    if len(parts) >= 2 and parts[0] == "git" and parts[1] in {"status", "diff", "show"}:
        return True, f"allow git {parts[1]}"

    return False, "not allowlisted"

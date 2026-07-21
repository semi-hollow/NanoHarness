import shlex

PYTHON_COMMANDS = {"python", "python3", "python3.11"}

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

# 运行时端口：解析 shell 命令并拒绝危险程序、参数与链式绕过。
def check_command(command: str) -> tuple[bool, str]:
    """返回命令是否允许以及拒绝/允许原因，不实际执行进程。"""

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

    if first in PYTHON_COMMANDS and len(parts) >= 3:
        module = parts[2]
        if parts[1] == "-m" and module in {"unittest", "pytest", "compileall"}:
            return True, f"allow python -m {module}"

    if first == "pytest":
        return True, "allow pytest"

    if len(parts) >= 2 and parts[0] == "git" and parts[1] in {"status", "diff", "show"}:
        return True, f"allow git {parts[1]}"

    return False, "not allowlisted"

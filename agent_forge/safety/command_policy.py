"""把命令文本收敛为无 shell 的窄验证/只读 Git allowlist。

系统角色：只判断 argv-shaped command 是否属于显式 allowlist；不启动进程、不检查
workspace 路径。
输入：command text；输出：allow boolean + reason。
相邻边界：RunCommandTool 校验路径；ExecutionEnvironment 执行 argv；shell 永远关闭。
核心阅读：``check_command`` 四步伪代码注释。
"""

import shlex

PYTHON_COMMANDS = {"python", "python3", "python3.11"}

DENY_PREFIX = {
    "rm",
    "del",
    "rmdir",
    "curl",
    "wget",
    "ssh",
    "scp",
    "chmod",
    "chown",
    "format",
    "mkfs",
    "powershell",
    "sudo",
    "shutdown",
    "reboot",
}

DENY_EXACT = {
    "git push",
    "git reset --hard",
    "powershell remove-item",
    "rm -rf",
}


# 策略入口：解析 argv-shaped 命令文本，拒绝危险程序和 shell 链式绕过。
def check_command(command: str) -> tuple[bool, str]:
    """返回窄命令策略结论，不启动 shell 或实际进程。

    伪代码：拒绝空值/已知危险命令 -> ``shlex`` 只负责切成 argv token ->
    阻断 shell operator/危险程序 -> 仅允许 Python 验证、pytest 和只读 Git。
    """

    # 1. 先处理无需解析即可拒绝的空值和精确危险命令。
    if not command.strip():
        return False, "empty command"

    normalized_command = command.strip().lower()
    if normalized_command in DENY_EXACT:
        return False, "dangerous command blocked"

    # 2. shlex 只用于生成 argv-shaped token；解析失败或出现 shell operator 都拒绝，
    # 后续执行方仍必须使用 shell=False。
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return False, f"invalid command: {exc}"

    if not parts:
        return False, "empty command"

    if any(
        any(operator in part for operator in ("|", ";", "&", "<", ">", "`", "$("))
        for part in parts
    ):
        return False, "shell operators are blocked; pass an argv-style command"

    first = parts[0].lower()
    if first in DENY_PREFIX:
        return False, "dangerous command blocked"

    # 3. allowlist 只开放固定 Python 验证入口、pytest 及只读 Git 检查。
    if first in PYTHON_COMMANDS and len(parts) >= 3:
        module = parts[2]
        if parts[1] == "-m" and module in {"unittest", "pytest", "compileall"}:
            return True, f"allow python -m {module}"

    if first == "pytest":
        return True, "allow pytest"

    if len(parts) >= 2 and parts[0] == "git" and parts[1] in {"status", "diff", "show"}:
        return True, f"allow git {parts[1]}"

    # 4. 新命令默认拒绝；扩能力必须显式进入 allowlist，而不是靠未命中黑名单。
    return False, "not allowlisted"

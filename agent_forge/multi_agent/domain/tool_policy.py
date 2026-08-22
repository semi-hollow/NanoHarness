"""定义 Planner 可选择、Worker 可获得、Finalizer 可读取的三档 Tool 集合。

这里回答的是“每种角色最多能看见哪些工具名”；具体 Worker 还会被任务自己的
``allowed_tools`` 与 ``write_scope`` 二次收窄，真正执行仍经过 Single-Agent
Runtime 的 Tool Governance。它不是第二套授权系统。
"""

from agent_forge.contracts import WORKSPACE_WRITE_TOOL_NAMES

READ_TOOLS = {
    "list_files",
    "read_file",
    "grep_search",
    "git_status",
    "git_diff",
    "python_validation",
    "ask_human",
}
FINALIZER_READ_TOOLS = {"git_status", "git_diff", "python_validation"}
WRITE_TOOLS = {*READ_TOOLS, *WORKSPACE_WRITE_TOOL_NAMES, "run_command"}


def fanout_available_tools() -> list[str]:
    """返回 Planner 可以提出、Worker policy 能执行的稳定工具名。"""

    return sorted(WRITE_TOOLS)


__all__ = [
    "FINALIZER_READ_TOOLS",
    "READ_TOOLS",
    "WRITE_TOOLS",
    "fanout_available_tools",
]

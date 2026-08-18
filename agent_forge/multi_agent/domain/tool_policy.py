"""Planner 与 Worker 共用的最小 fanout 工具边界。"""

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

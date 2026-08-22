"""NanoHarness 的本地操作台展示层。

这里不实现 Agent 能力；它只把 Harness 的实时事件和人工控制端口呈现为 TUI。
"""

from apps.operator_console.api import run_console_from_args

__all__ = ["run_console_from_args"]

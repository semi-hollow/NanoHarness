"""Runtime 控制面。

首次阅读顺序：
    ``application/agent_loop.py`` 只看四阶段编排；``application/session.py`` 看一次 run 的数据；
    ``application/tool_execution.py`` 看工具治理；``application/run_lifecycle.py`` 看 checkpoint/HITL/stop。
    ``control.py``、``hooks.py`` 和 ``execution_environment.py`` 是下一级策略 owner。

不要在这里导入 ``AgentLoop``。Package root 只暴露轻量控制类型；完整用例从
``runtime.api`` 进入，避免初始化时形成循环依赖。
"""

from .config import RuntimeConfig
from .control import ExecutionBudget, FailureKind, FailureSignal, StepController

__all__ = [
    "ExecutionBudget",
    "FailureKind",
    "FailureSignal",
    "RuntimeConfig",
    "StepController",
]

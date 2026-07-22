"""AgentLoop 查询运行中人工控制信号的端口。

本文件只有 ``Protocol`` 契约，不负责接收用户输入。真实输入端是公开
``agent_forge.RunController.pause/cancel/steer``；它作为
``HarnessExtensions.run_control`` 注入后，由 Application 通过下面两个只读方法消费。
CLI 默认装配 ``NoopRunControl``，因此当前 live steer 是嵌入式 SDK 能力，不是终端交互命令。
"""

from __future__ import annotations

from typing import Protocol

from agent_forge.runtime.domain.run_control import RunControlSignal


class RunControlPort(Protocol):
    """控制信号的 Runtime 读取侧契约，不是具体队列实现。

    实现地图：``RunController`` 是线程安全内存 Adapter；``NoopRunControl`` 是无输入
    Adapter；``ApplyRunControl.check`` 把读出的信号转换为状态迁移或下一轮 user message。
    Python 本可用结构化类型；两个关键 Adapter 仍显式继承本类，让 IDE Hierarchy
    能直接跳到实现。
    """

    def take_terminal(self, run_id: str) -> RunControlSignal | None:
        """原子取走一次 pause/cancel；没有信号时返回空。"""

        ...

    def drain_steers(self, run_id: str) -> list[RunControlSignal]:
        """按提交顺序取走等待注入下一轮上下文的 steer。"""

        ...

"""AgentLoop 查询运行中人工控制与 Runtime coordination 的读取端口。

本文件只有 ``Protocol`` 契约，不接收输入。操作员通过公开
``agent_forge.RunController.pause/cancel/steer`` 提交控制；Multi-Agent Worker 通过
``LiveHandoffRunControl`` 接收非人工协调证据。两条来源都由 composition root 注入，
再由 Application 通过下面三个只读方法消费。CLI 默认装配 ``NoopRunControl``，
因此当前 operator live steer 是嵌入式 SDK 能力，不是终端交互命令。
"""

from __future__ import annotations

from typing import Protocol

from agent_forge.runtime.domain.run_control import (
    RunControlSignal,
    RuntimeCoordinationSignal,
)


class RunControlPort(Protocol):
    """人工控制与 Runtime coordination 的统一读取侧契约，不是队列实现。

    实现地图：``RunController`` 提供线程安全的人工控制；``LiveHandoffRunControl``
    投影 Worker mailbox；``NoopRunControl`` 让两类输入都为空。
    ``RunControlHandler.consume_pending_signals`` 再把读出的信号转换为状态迁移或
    下一轮模型输入。三个关键 Adapter 都显式继承本类，IDE Hierarchy 可直接跳转。
    """

    def take_terminal(self, run_id: str) -> RunControlSignal | None:
        """原子取走一次 pause/cancel；没有信号时返回空。"""

        ...

    def drain_steers(self, run_id: str) -> list[RunControlSignal]:
        """按提交顺序取走等待注入下一轮上下文的 steer。"""

        ...

    def drain_coordination(
        self,
        run_id: str,
        *,
        boundary: str,
    ) -> list[RuntimeCoordinationSignal]:
        """取出只允许在命名模型边界进入下一输入的非人工协调证据。"""

        ...

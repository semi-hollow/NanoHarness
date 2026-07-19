"""AgentLoop 查询运行中人工控制信号的端口。"""

from __future__ import annotations

from typing import Protocol

from agent_forge.runtime.domain.run_control import RunControlSignal


class RunControlPort(Protocol):
    """将控制信号的并发存储与 AgentLoop 控制流隔离。"""

    def take_terminal(self, run_id: str) -> RunControlSignal | None:
        """原子取走一次 pause/cancel；没有信号时返回空。"""

    def drain_steers(self, run_id: str) -> list[RunControlSignal]:
        """按提交顺序取走等待注入下一轮上下文的 steer。"""

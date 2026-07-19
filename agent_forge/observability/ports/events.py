"""实时 Runtime Event 的输出端口。"""

from typing import Protocol

from agent_forge.observability.domain.live_event import RuntimeEvent


class RuntimeEventListener(Protocol):
    """同步接收有序事件；实现应保持轻量或自行转交队列。"""

    def on_event(self, event: RuntimeEvent) -> None:
        """处理一个已经按 StreamPolicy 脱敏的事件。"""

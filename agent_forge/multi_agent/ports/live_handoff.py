"""协作式 Live Handoff 机制的 Ports。"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from ..domain.fanout import SubagentTask
from ..domain.live_handoff import (
    LiveHandoffEvent,
    LiveHandoffSummary,
    LiveWorkerCandidate,
)


class LiveWorkerContextPort(Protocol):
    """运行中 Worker 唯一可见的协作接口。"""

    @property
    def task_id(self) -> str:
        """返回 Runtime 绑定的稳定任务身份。"""

        ...

    def publish(self, event: LiveHandoffEvent) -> bool:
        """提出结构化事件，是否接受仍由 Runtime 校验。"""

        ...

    def drain_mailbox(self, *, boundary: str) -> list[LiveHandoffEvent]:
        """只在命名的 Turn 安全边界消费消息。"""

        ...

    def record_action(self, action: str, **data: Any) -> None:
        """为机制 timeline 记录一个有界 Worker 动作。"""

        ...


class LiveHandoffWorkerPort(Protocol):
    """运行一个隔离协作 Worker，直到产生终态候选。"""

    def run_worker(
        self,
        task: SubagentTask,
        context: LiveWorkerContextPort,
    ) -> LiveWorkerCandidate:
        """执行多个 Turn，且只在 model/tool transaction 之间使用 ``context``。"""

        ...


class LiveIntegrationPort(Protocol):
    """依赖新鲜度检查通过后，校验组合候选结果。"""

    def validate(
        self,
        candidates: Mapping[str, LiveWorkerCandidate],
    ) -> tuple[bool, str]:
        """返回真实最终测试结果和简短证据。"""

        ...


class LiveHandoffArtifactPort(Protocol):
    """保存可审计 append-only timeline 与最终 summary 投影。"""

    def append_timeline(self, record: Mapping[str, Any]) -> None:
        """追加并 flush 一条不可变 timeline 记录。"""

        ...

    def write_summary(self, summary: LiveHandoffSummary) -> str:
        """原子写入最终 canonical summary 并返回路径。"""

        ...

    def close(self) -> None:
        """Flush 并关闭该 Run 的 timeline writer。"""

        ...

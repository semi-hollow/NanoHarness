"""``FanoutCoordinator`` 的单一依赖容器。

它只把 composition root 创建的 outbound Ports 交给 Application，不创建 Adapter、
不保存运行状态，也不形成第二套 wiring。
"""

from dataclasses import dataclass

from ..ports import (
    FanoutArtifactPort,
    FanoutReplannerPort,
    FanoutWorkerPort,
    FanoutWorkspacePort,
    LiveFanoutEvents,
)


@dataclass(frozen=True)
class LiveFanoutDependencies:
    """由 ``multi_agent.wiring`` 装配的一组 outbound ports。"""

    events: LiveFanoutEvents
    workspace: FanoutWorkspacePort
    artifacts: FanoutArtifactPort
    workers: FanoutWorkerPort
    replanner: FanoutReplannerPort | None = None

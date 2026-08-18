"""Orchestration application 的显式依赖。"""

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

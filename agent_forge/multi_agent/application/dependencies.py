"""Orchestration application 的显式依赖。"""

from dataclasses import dataclass

from ..ports import (
    CandidatePatchPort,
    CoordinatorEventSink,
    FanoutArtifactPort,
    FanoutWorkerPort,
    FanoutWorkspacePort,
    LiveFanoutEvents,
    RoleArtifactPort,
    RoleRunnerPort,
)


@dataclass(frozen=True)
class LiveFanoutDependencies:
    """由 ``multi_agent.wiring`` 装配的一组 outbound ports。"""

    events: LiveFanoutEvents
    workspace: FanoutWorkspacePort
    artifacts: FanoutArtifactPort
    workers: FanoutWorkerPort


@dataclass(frozen=True)
class SequentialCoordinatorDependencies:
    """顺序角色编排所需的 outbound ports。"""

    events: CoordinatorEventSink
    artifacts: RoleArtifactPort
    role_runner: RoleRunnerPort
    candidate_patch: CandidatePatchPort

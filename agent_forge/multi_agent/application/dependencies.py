"""Orchestration application 的显式依赖。"""

from dataclasses import dataclass

from ..ports import (
    CandidateDiffPort,
    CoordinatorEventSink,
    FanoutArtifactPort,
    FanoutWorkerPort,
    FanoutWorkspacePort,
    LiveHandoffArtifactPort,
    LiveHandoffWorkerPort,
    LiveIntegrationPort,
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
class LiveHandoffDependencies:
    """协作 Worker、durable timeline 与最终 integration validator。"""

    artifacts: LiveHandoffArtifactPort
    workers: LiveHandoffWorkerPort
    integration: LiveIntegrationPort


@dataclass(frozen=True)
class SequentialCoordinatorDependencies:
    """顺序角色编排所需的 outbound ports。"""

    events: CoordinatorEventSink
    artifacts: RoleArtifactPort
    role_runner: RoleRunnerPort
    candidate_diff: CandidateDiffPort

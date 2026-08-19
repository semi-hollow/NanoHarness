"""Multi-Agent 用例的统一依赖装配点。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.execution_environment import ExecutionEnvironment
from agent_forge.runtime.ports.model import ModelPort
from agent_forge.tools.registry import ToolRegistry

from .adapters.fanout_files import FanoutFileRepository
from .adapters.live_handoff_files import JsonlLiveHandoffRepository
from .adapters.artifact_files import FileArtifactRepository
from .adapters.git_workspace import GitFanoutWorkspace
from .adapters.local_worker import LocalAgentWorkerAdapter
from .adapters.role_runtime import AgentLoopRoleRunner, GitCandidateDiff
from .application.coordinator import MultiAgentCoordinator
from .application.live_handoff import LiveHandoffCoordinator
from .application.dependencies import (
    LiveFanoutDependencies,
    LiveHandoffDependencies,
    SequentialCoordinatorDependencies,
)
from .application.live_fanout import LiveFanoutCoordinator
from .domain.live import FanoutPlan
from .domain.live_handoff import LiveHandoffPlan
from .domain.models import AgentProfile
from .ports import (
    CoordinatorEventSink,
    LiveFanoutEvents,
    LiveHandoffWorkerPort,
    LiveIntegrationPort,
)

RegistryFactory = Callable[[Path, ExecutionEnvironment], ToolRegistry]
LLMFactory = Callable[[], ModelPort]


# 核心数据：装配真实 fanout coordinator 所需的计划、Runtime 与 factory。
@dataclass(frozen=True)
class LiveFanoutBuildRequest:
    """Composition root 的完整输入，不在 Application 内创建 Adapter。"""

    plan: FanoutPlan
    base_config: RuntimeConfig
    trace: LiveFanoutEvents
    run_dir: str | Path
    llm_factory: LLMFactory
    registry_factory: RegistryFactory
    max_workers: int = 4
    resume_from: str | Path | None = None


@dataclass(frozen=True)
class LiveHandoffBuildRequest:
    """Composition request for the cooperative milestone scheduler."""

    plan: LiveHandoffPlan
    scenario: str
    mode: str
    run_dir: str | Path
    workers: LiveHandoffWorkerPort
    integration: LiveIntegrationPort
    max_workers: int = 4
    timeout_seconds: float = 30.0
    run_id: str | None = None


# 核心数据：装配顺序多角色 coordinator 所需的运行对象。
@dataclass(frozen=True)
class SequentialCoordinatorBuildRequest:
    """任务、profile、共享 Runtime 与 artifact 位置。"""

    task: str
    profile: AgentProfile
    runtime_config: RuntimeConfig
    trace: CoordinatorEventSink
    registry: ToolRegistry
    llm: ModelPort
    run_dir: str | Path
    max_revision_rounds: int | None = None


# 主要入口：装配 DAG、隔离 workspace、真实 AgentLoop worker 和 finalizer。
def build_live_fanout(request: LiveFanoutBuildRequest) -> LiveFanoutCoordinator:
    """装配 Git、文件 artifact 和真实 AgentLoop worker adapters。"""

    workspace = GitFanoutWorkspace(request.base_config.workspace)
    artifacts = FanoutFileRepository(request.run_dir)
    workers = LocalAgentWorkerAdapter(
        plan=request.plan,
        base_config=request.base_config,
        run_root=artifacts.root,
        run_id=request.trace.run_id,
        base_head=workspace.head(),
        llm_factory=request.llm_factory,
        registry_factory=request.registry_factory,
    )
    return LiveFanoutCoordinator(
        plan=request.plan,
        base_config=request.base_config,
        dependencies=LiveFanoutDependencies(
            events=request.trace,
            workspace=workspace,
            artifacts=artifacts,
            workers=workers,
        ),
        max_workers=request.max_workers,
        resume_from=str(request.resume_from) if request.resume_from else None,
    )


def build_live_handoff(request: LiveHandoffBuildRequest) -> LiveHandoffCoordinator:
    """Assemble the durable timeline, governed Runtime, workers, and validator."""

    return LiveHandoffCoordinator(
        plan=request.plan,
        scenario=request.scenario,
        mode=request.mode,
        dependencies=LiveHandoffDependencies(
            artifacts=JsonlLiveHandoffRepository(request.run_dir),
            workers=request.workers,
            integration=request.integration,
        ),
        max_workers=request.max_workers,
        timeout_seconds=request.timeout_seconds,
        run_id=request.run_id,
    )


# 主要入口：装配顺序角色 profile、artifact store 与共享 Runtime factory。
def build_multi_agent_coordinator(
    request: SequentialCoordinatorBuildRequest,
) -> MultiAgentCoordinator:
    """装配角色 Runtime、Artifact repository 和 candidate diff 查询。"""

    workspace = GitFanoutWorkspace(request.runtime_config.workspace)
    return MultiAgentCoordinator(
        request.task,
        request.profile,
        request.runtime_config,
        SequentialCoordinatorDependencies(
            events=request.trace,
            artifacts=FileArtifactRepository(Path(request.run_dir)),
            role_runner=AgentLoopRoleRunner(
                request.trace,
                request.registry,
                request.llm,
            ),
            candidate_diff=GitCandidateDiff(workspace),
        ),
        run_dir=request.run_dir,
        max_revision_rounds=request.max_revision_rounds,
    )

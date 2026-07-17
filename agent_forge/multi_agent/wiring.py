"""Multi-Agent 用例的统一依赖装配点。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from agent_forge.observability.adapters.json_trace import TraceRecorder
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.execution_environment import ExecutionEnvironment
from agent_forge.runtime.llm_client import LLMClient
from agent_forge.tools.registry import ToolRegistry

from .adapters.fanout_files import FanoutFileRepository
from .adapters.artifact_files import FileArtifactRepository
from .adapters.git_workspace import GitFanoutWorkspace
from .adapters.local_worker import LocalAgentWorkerAdapter
from .adapters.role_runtime import AgentLoopRoleRunner, GitCandidatePatch
from .application.coordinator import MultiAgentCoordinator
from .application.dependencies import (
    LiveFanoutDependencies,
    SequentialCoordinatorDependencies,
)
from .application.live_fanout import LiveFanoutCoordinator
from .domain.live import FanoutPlan
from .domain.models import AgentProfile

RegistryFactory = Callable[[Path, ExecutionEnvironment], ToolRegistry]
LLMFactory = Callable[[], LLMClient]

# 主要入口：装配 DAG、隔离 workspace、真实 AgentLoop worker 和 finalizer。
def build_live_fanout(
    *,
    plan: FanoutPlan,
    base_config: RuntimeConfig,
    trace: TraceRecorder,
    run_dir: str | Path,
    llm_factory: LLMFactory,
    registry_factory: RegistryFactory,
    max_workers: int = 4,
    resume_from: str | Path | None = None,
) -> LiveFanoutCoordinator:
    """装配 Git、文件 artifact 和真实 AgentLoop worker adapters。"""

    workspace = GitFanoutWorkspace(base_config.workspace)
    artifacts = FanoutFileRepository(run_dir)
    workers = LocalAgentWorkerAdapter(
        plan=plan,
        base_config=base_config,
        run_root=artifacts.root,
        run_id=trace.run_id,
        base_head=workspace.head(),
        llm_factory=llm_factory,
        registry_factory=registry_factory,
    )
    return LiveFanoutCoordinator(
        plan=plan,
        base_config=base_config,
        dependencies=LiveFanoutDependencies(
            events=trace,
            workspace=workspace,
            artifacts=artifacts,
            workers=workers,
        ),
        max_workers=max_workers,
        resume_from=str(resume_from) if resume_from else None,
    )

# 主要入口：装配顺序角色 profile、artifact store 与共享 Runtime factory。
def build_multi_agent_coordinator(
    task: str,
    profile: AgentProfile,
    runtime_config: RuntimeConfig,
    trace: TraceRecorder,
    registry: ToolRegistry,
    llm: LLMClient,
    *,
    run_dir: str | Path,
    max_revision_rounds: int | None = None,
) -> MultiAgentCoordinator:
    """装配角色 Runtime、Artifact repository 和 candidate patch 查询。"""

    workspace = GitFanoutWorkspace(runtime_config.workspace)
    return MultiAgentCoordinator(
        task,
        profile,
        runtime_config,
        SequentialCoordinatorDependencies(
            events=trace,
            artifacts=FileArtifactRepository(Path(run_dir)),
            role_runner=AgentLoopRoleRunner(trace, registry, llm),
            candidate_patch=GitCandidatePatch(workspace),
        ),
        run_dir=run_dir,
        max_revision_rounds=max_revision_rounds,
    )

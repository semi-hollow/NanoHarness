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
from .adapters.git_workspace import GitFanoutWorkspace
from .adapters.local_worker import LocalAgentWorkerAdapter
from .application.dependencies import LiveFanoutDependencies
from .application.live_fanout import LiveFanoutCoordinator
from .domain.live import FanoutPlan
from .ports import FanoutReplannerPort, LiveFanoutEvents

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
    replanner: FanoutReplannerPort | None = None
    allow_replan: bool = True


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
            replanner=request.replanner,
        ),
        max_workers=request.max_workers,
        resume_from=str(request.resume_from) if request.resume_from else None,
        allow_replan=request.allow_replan,
    )

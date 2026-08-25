"""Multi-Agent 用例的唯一 composition root。

输入：typed plan、RuntimeConfig、trace 和两个 factory。
输出：已经连接 Git workspace、artifact repository 和真实 AgentLoop Worker 的
``FanoutCoordinator``。本文件只装配，不复制调度或治理逻辑。

折叠导航：1 BuildRequest；2 Composition root。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.adapters.execution_environment import ExecutionEnvironment
from agent_forge.runtime.ports.model import ModelPort
from agent_forge.tools.registry import ToolRegistry

from .adapters.fanout_files import FanoutFileRepository
from .adapters.git_workspace import GitFanoutWorkspace
from .adapters.local_worker import LocalAgentWorkerAdapter
from .application.dependencies import FanoutDependencies
from .application.fanout import FanoutCoordinator
from .domain.live import FanoutPlan
from .ports import FanoutEvents

RegistryFactory = Callable[[Path, ExecutionEnvironment], ToolRegistry]
LLMFactory = Callable[[], ModelPort]


# region 1. BuildRequest：调用方需要提供的完整、显式装配输入
# 核心数据：装配真实 fanout coordinator 所需的计划、Runtime 与 factory。
@dataclass(frozen=True)
class FanoutBuildRequest:
    """Composition root 的完整输入，不在 Application 内创建 Adapter。"""

    plan: FanoutPlan
    base_config: RuntimeConfig
    trace: FanoutEvents
    run_dir: str | Path
    llm_factory: LLMFactory
    registry_factory: RegistryFactory
    max_workers: int = 4
    resume_from: str | Path | None = None
# endregion 1. BuildRequest 结束


# region 2. Composition root：创建三个 Adapter，再把 Ports 交给唯一 Coordinator
# 主要入口：装配 DAG、隔离 workspace、真实 AgentLoop worker 和 finalizer。
def build_fanout(request: FanoutBuildRequest) -> FanoutCoordinator:
    """装配 Git、文件 artifact 和真实 AgentLoop Worker adapters。

    这里是外部调用进入 Multi-Agent execution half 的第一跳；执行顺序必须继续进入
    ``FanoutCoordinator.run``，不能在 wiring 中提前启动 Worker。
    """

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
    return FanoutCoordinator(
        plan=request.plan,
        base_config=request.base_config,
        dependencies=FanoutDependencies(
            events=request.trace,
            workspace=workspace,
            artifacts=artifacts,
            workers=workers,
        ),
        max_workers=request.max_workers,
        resume_from=str(request.resume_from) if request.resume_from else None,
    )
# endregion 2. Composition root 结束

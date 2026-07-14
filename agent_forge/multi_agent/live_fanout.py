"""兼容入口：旧构造签名委托给 Orchestration composition root。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from agent_forge.observability.trace import TraceRecorder
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.execution_environment import ExecutionEnvironment
from agent_forge.runtime.llm_client import LLMClient
from agent_forge.tools.registry import ToolRegistry

from .adapters.local_worker import _finalizer_task
from .adapters.plan_files import load_fanout_plan
from .application.dependencies import LiveFanoutDependencies
from .application.live_fanout import (
    LiveFanoutCoordinator as ApplicationLiveFanoutCoordinator,
)
from .domain.live import (
    FanoutPlan as DomainFanoutPlan,
    LiveFanoutSummary,
    LiveSubagentResult,
)
from .wiring import build_live_fanout


class FanoutPlan(DomainFanoutPlan):
    """保留历史 ``FanoutPlan.load`` 的薄兼容类型。"""

    @classmethod
    def load(cls, path: str | Path) -> "FanoutPlan":
        plan = load_fanout_plan(path)
        return cls(goal=plan.goal, tasks=plan.tasks)


class LiveFanoutCoordinator(ApplicationLiveFanoutCoordinator):
    """保留迁移前构造签名，运行逻辑仍位于 Application。"""

    def __init__(
        self,
        *,
        plan: DomainFanoutPlan,
        base_config: RuntimeConfig,
        trace: TraceRecorder,
        run_dir: str | Path,
        llm_factory: Callable[[], LLMClient],
        registry_factory: Callable[[Path, ExecutionEnvironment], ToolRegistry],
        max_workers: int = 4,
        resume_from: str | Path | None = None,
    ) -> None:
        assembled = build_live_fanout(
            plan=plan,
            base_config=base_config,
            trace=trace,
            run_dir=run_dir,
            llm_factory=llm_factory,
            registry_factory=registry_factory,
            max_workers=max_workers,
            resume_from=resume_from,
        )
        super().__init__(
            plan=assembled.plan,
            base_config=assembled.base_config,
            dependencies=LiveFanoutDependencies(
                events=assembled.events,
                workspace=assembled.workspace,
                artifacts=assembled.artifacts,
                workers=assembled.workers,
            ),
            max_workers=assembled.max_workers,
            resume_from=assembled.resume_from,
        )


__all__ = [
    "FanoutPlan",
    "LiveFanoutCoordinator",
    "LiveFanoutSummary",
    "LiveSubagentResult",
]

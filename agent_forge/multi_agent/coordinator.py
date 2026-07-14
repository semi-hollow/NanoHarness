"""兼容入口：旧构造签名委托给 Orchestration composition root。"""

from __future__ import annotations

from pathlib import Path

from agent_forge.observability.trace import TraceRecorder
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import LLMClient
from agent_forge.tools.registry import ToolRegistry

from .application.coordinator import MultiAgentCoordinator as ApplicationCoordinator
from .application.dependencies import SequentialCoordinatorDependencies
from .domain.models import AgentProfile
from .wiring import build_multi_agent_coordinator


class MultiAgentCoordinator(ApplicationCoordinator):
    """保留迁移前构造方式，业务流程仍位于 Application。"""

    def __init__(
        self,
        task: str,
        profile: AgentProfile,
        runtime_config: RuntimeConfig,
        trace: TraceRecorder,
        registry: ToolRegistry,
        llm: LLMClient,
        *,
        run_dir: str | Path,
        max_revision_rounds: int | None = None,
    ) -> None:
        assembled = build_multi_agent_coordinator(
            task,
            profile,
            runtime_config,
            trace,
            registry,
            llm,
            run_dir=run_dir,
            max_revision_rounds=max_revision_rounds,
        )
        super().__init__(
            assembled.task,
            assembled.profile,
            assembled.base_config,
            SequentialCoordinatorDependencies(
                events=assembled.trace,
                artifacts=assembled.store,
                role_runner=assembled.role_runner,
                candidate_patch=assembled.candidate_patch,
            ),
            run_dir=assembled.run_dir,
            max_revision_rounds=assembled.max_revision_rounds,
        )


__all__ = ["MultiAgentCoordinator"]

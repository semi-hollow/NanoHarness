"""兼容入口：旧调用签名委托给新的 Runtime composition root。

新代码应从 ``runtime.api`` 导入 ``build_agent_loop``。该类仅为已有用户和测试保留，
不再拥有运行逻辑。
"""

from __future__ import annotations

from agent_forge.observability.trace import TraceRecorder
from agent_forge.runtime.application.agent_loop import AgentLoop as ApplicationAgentLoop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import LLMClient
from agent_forge.runtime.wiring import build_runtime_dependencies
from agent_forge.tools.registry import ToolRegistry


class AgentLoop(ApplicationAgentLoop):
    """Backward-compatible constructor for the pre-layered public import."""

    def __init__(
        self,
        config: RuntimeConfig,
        trace: TraceRecorder,
        registry: ToolRegistry,
        llm: LLMClient | None = None,
    ) -> None:
        super().__init__(
            config,
            build_runtime_dependencies(config, trace, registry, llm),
        )


__all__ = ["AgentLoop"]

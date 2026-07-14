"""顺序角色到规范 AgentLoop 的 adapter。"""

from __future__ import annotations

from agent_forge.observability.adapters.json_trace import TraceRecorder
from agent_forge.runtime.api import build_agent_loop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.llm_client import LLMClient
from agent_forge.tools.registry import ToolRegistry


class AgentLoopRoleRunner:
    """为每个角色创建工具子集并复用同一个 Runtime。"""

    def __init__(
        self,
        trace: TraceRecorder,
        registry: ToolRegistry,
        llm: LLMClient,
    ) -> None:
        self.trace = trace
        self.registry = registry
        self.llm = llm

    def run_role(
        self,
        *,
        config: RuntimeConfig,
        allowed_tools: list[str],
        task: str,
        agent_name: str,
    ) -> str:
        registry = ToolRegistry()
        for tool_name in allowed_tools:
            tool = self.registry.get(tool_name)
            if tool is not None:
                registry.register(tool)
        return build_agent_loop(config, self.trace, registry, self.llm).run(
            task,
            agent_name=agent_name,
        )


class GitCandidatePatch:
    """查询 workspace candidate patch 的降级安全 adapter。"""

    def __init__(self, workspace: GitFanoutWorkspace) -> None:
        self.workspace = workspace

    def exists(self) -> bool:
        try:
            return bool(self.workspace.diff().strip())
        except (OSError, RuntimeError, TimeoutError):
            return False

from .git_workspace import GitFanoutWorkspace  # noqa: E402

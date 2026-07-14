"""基于文件系统的仓库上下文组装 Adapter。"""

from __future__ import annotations

from agent_forge.context.context_builder import ContextBuildReport, build_context_report
from agent_forge.context.contracts import ContextMemory
from agent_forge.context.repo_map import build_repo_map
from agent_forge.contracts import ToolSchema


class RepositoryContextAssembler:
    """扫描 workspace，并构造 Runtime 消费的上下文报告。"""

    # RUNTIME PORT: isolate repository reads behind ContextAssemblerPort.
    def build(
        self,
        *,
        task: str,
        workspace: str,
        memory: ContextMemory,
        tools: list[ToolSchema],
        active_skill_cards: list[str],
        max_chars: int,
        permission_summary: str,
    ) -> ContextBuildReport:
        """读取仓库证据并返回有界上下文报告。"""

        repo_map = build_repo_map(workspace)
        return build_context_report(
            task,
            repo_map,
            memory,
            docs=repo_map.splitlines(),
            root=workspace,
            tools=tools,
            active_skill_cards=active_skill_cards,
            max_chars=max_chars,
            permission_summary=permission_summary,
        )

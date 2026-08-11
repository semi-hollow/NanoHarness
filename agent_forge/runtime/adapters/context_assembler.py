"""基于文件系统的仓库上下文组装 Adapter。"""

from __future__ import annotations

from agent_forge.context.context_builder import (
    ContextBuildPolicy,
    ContextBuildReport,
    ContextBuildRequest,
    build_context_report,
)
from agent_forge.context.repo_map import build_repo_map
from agent_forge.runtime.ports.context import ContextAssemblerPort, ContextAssemblyRequest


class RepositoryContextAssembler(ContextAssemblerPort):
    """扫描 workspace，并构造 Runtime 消费的上下文报告。"""

    # 运行时端口：读取 repository 事实并返回有预算的类型化 ContextReport。
    def build(self, request: ContextAssemblyRequest) -> ContextBuildReport:
        """先生成仓库结构图，再组装受字符预算约束的类型化 Context 报告。

        报告合并任务、指令、工作记忆、Skill 和 Tool 信息；本 Adapter 只读取并组装证据，
        不选择 Tool、不压缩会话历史，也不决定模型行为。
        """

        repo_map = build_repo_map(request.workspace)
        return build_context_report(
            ContextBuildRequest(
                task=request.task,
                repo_map=repo_map,
                working_memory=request.working_memory,
                root=request.workspace,
                tools=request.tools,
                active_skill_cards=request.active_skill_cards,
                policy=ContextBuildPolicy(
                    max_chars=request.max_chars,
                    permission_summary=request.permission_summary,
                ),
                instruction_target=request.instruction_target,
                global_instruction_files=request.global_instruction_files,
                runtime_instructions=request.runtime_instructions,
                instruction_max_bytes=request.instruction_max_bytes,
            )
        )

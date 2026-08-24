"""基于文件系统的仓库上下文组装 Adapter。"""

from __future__ import annotations

from pathlib import Path

from agent_forge.context.application.context_builder import (
    StableTurnContextBuildRequest,
    TurnSystemContextBuildPolicy,
    TurnSystemContextBuildReport,
    TurnSystemContextBuildRequest,
    build_turn_system_context,
    build_stable_turn_context,
)
from agent_forge.context.adapters.repository_map import (
    build_repo_map,
    repository_structure_revision,
)
from agent_forge.runtime.ports.context import (
    StableTurnContextRequest,
    StableTurnContextView,
    TurnSystemContextAssemblerPort,
    TurnSystemContextRequest,
)


class RepositoryTurnSystemContextAssembler(TurnSystemContextAssemblerPort):
    """扫描 workspace，并构造 Runtime 消费的上下文报告。"""

    def __init__(self) -> None:
        self._repo_maps: dict[str, tuple[int, str]] = {}

    def freeze_stable(
        self,
        request: StableTurnContextRequest,
    ) -> StableTurnContextView:
        """解析一次稳定输入；调用方负责把结果写入 Turn snapshot。"""

        return build_stable_turn_context(
            StableTurnContextBuildRequest(
                root_task=request.root_task,
                root=request.workspace,
                base_tool_schemas=request.base_tool_schemas,
                active_skill_cards=request.active_skill_cards,
                long_term_memory=request.long_term_memory,
                policy=TurnSystemContextBuildPolicy(max_chars=request.max_chars),
                instruction_target=request.instruction_target,
                global_instruction_files=request.global_instruction_files,
                runtime_instructions=request.runtime_instructions,
                instruction_max_bytes=request.instruction_max_bytes,
                system_prompt_profile=request.system_prompt_profile,
            )
        )

    # 运行时端口：读取 repository 事实并返回有预算的类型化 ContextReport。
    def build(self, request: TurnSystemContextRequest) -> TurnSystemContextBuildReport:
        """先生成仓库结构图，再组装受字符预算约束的类型化 Context 报告。

        报告合并任务、指令、工作记忆、Skill 和 Tool 信息；本 Adapter 只读取并组装证据，
        不选择 Tool、不压缩会话历史，也不决定模型行为。
        """

        # Repo Map 只描述路径；正文修改继续动态读取，结构版本不变时无需重复 rglob。
        workspace = str(Path(request.workspace).expanduser().resolve())
        structure_revision = repository_structure_revision(workspace)
        cached_repo_map = self._repo_maps.get(workspace)
        if cached_repo_map is None or cached_repo_map[0] != structure_revision:
            cached_repo_map = (structure_revision, build_repo_map(workspace))
            self._repo_maps[workspace] = cached_repo_map
        repo_map = cached_repo_map[1]
        return build_turn_system_context(
            TurnSystemContextBuildRequest(
                turn_focus=request.turn_focus,
                stable_system_prefix=request.stable_system_prefix,
                repo_map=repo_map,
                working_memory=request.working_memory,
                root=request.workspace,
                tool_schemas=request.tool_schemas,
                policy=TurnSystemContextBuildPolicy(
                    max_chars=request.max_chars,
                    permission_summary=request.permission_summary,
                ),
                frozen_instruction_paths=request.frozen_instruction_paths,
            )
        )

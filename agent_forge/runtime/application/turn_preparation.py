"""一次模型 turn 的工具路由与上下文组装。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_forge.context.application import (
    ContextWindowManager,
    ContextWindowRequest,
    ContextWindowResult,
    PromptBudget,
)
from agent_forge.context.domain import SessionDigest
from agent_forge.contracts import ToolSchema
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.domain.task import TaskCheckpointUpdate, TaskRunStatus
from agent_forge.runtime.ports import (
    ContextAssemblerPort,
    ContextAssemblyRequest,
    ContextReportView,
    EnvironmentPort,
    EventSink,
    ToolGateway,
)
from agent_forge.tools.tool_router import ToolRoute, ToolRouter, ToolRoutingRequest


@dataclass(frozen=True, kw_only=True)
class PreparedTurn:
    """一次 LLM 调用所需的完整、可度量输入。"""

    step: int
    context_message: Message
    messages_for_llm: list[Message]
    schemas: list[ToolSchema]
    allowed_tool_names: set[str]
    history_chars: int
    tool_schema_chars: int
    estimated_prompt_tokens: int
    compacted: bool
    session_digest: SessionDigest | None


class TurnPreparation:
    """构造模型输入，但不调用模型也不执行工具。"""

    def __init__(
        self,
        config: RuntimeConfig,
        trace: EventSink,
        context: ContextAssemblerPort,
        tools: ToolGateway,
        environment: EnvironmentPort,
        model_capabilities: ModelCapabilities,
    ) -> None:
        self.config = config
        self.trace = trace
        self.context_assembler = context
        self.tool_gateway = tools
        self.execution_environment = environment
        self.model_capabilities = model_capabilities
        self.tool_router = ToolRouter()
        effective_context_window = max(
            1_024,
            min(
                int(config.max_prompt_tokens),
                model_capabilities.context_window,
            ),
        )
        self.context_window = ContextWindowManager(
            PromptBudget(
                max_prompt_tokens=effective_context_window,
                reserved_output_tokens=min(
                    max(0, int(config.reserved_output_tokens)),
                    effective_context_window - 512,
                ),
            )
        )

    # 主要入口：为当前 turn 路由工具、组装上下文并执行会话窗口治理。
    def prepare_turn(
        self,
        session: AgentRunSession,
        step: int,
        *,
        force_compaction: bool = False,
    ) -> PreparedTurn:
        """为 ``AgentLoop`` 生成一次可直接提交给模型的 ``PreparedTurn``。

        流程位置：每个 turn 的上下文、工具集合与预算汇合点。
        规范上游：``AgentLoop._run_turn``。
        下一 owner：模型调用边界。
        状态与证据：RUNNING checkpoint、路由、裁剪与 token 预算事件。
        系统不变量：模型 schema 必须匹配 ``allowed_tool_names``，且压缩不能拆事务。
        删除/内联影响：会拆散模型请求的 context/tool/budget 一致性边界。
        """

        # region 1. Turn 起点：先持久化恢复位置，再准备任何模型输入
        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(
                status=TaskRunStatus.RUNNING,
                current_step=step,
                messages_count=len(session.messages),
                observations_count=len(session.observations),
                resume_hint=(
                    "Rerun with --resume-state to seed this task state into a continuation."
                ),
            )
        )
        # endregion 1. Turn 起点结束

        # region 2. 工具路由：收敛模型可见 schema，并保持权限摘要一致
        tool_route = self.tool_router.route(
            ToolRoutingRequest(
                task=session.task,
                schemas=self.tool_gateway.schemas(),
                step=step,
                agent_name=session.agent_name,
                skill_tool_names=session.skill_tool_names,
                mode=self.config.tool_routing_mode,
            )
        )
        visible_tool_schemas: list[ToolSchema] = tool_route.schemas
        allowed_tool_names = set(tool_route.allowed_names)
        model_permission_summary = (
            "read/list/grep allowed; replace_text/write_file asks approval; "
            "dangerous commands denied; "
            f"{self.execution_environment.render_boundary_summary()}"
        )
        if step == session.max_iterations:
            visible_tool_schemas = []
            allowed_tool_names = set()
            model_permission_summary += (
                "; final step: no more tool calls are available, provide the best "
                "evidence-based final answer and clearly mark unverified items"
            )
        # endregion 2. 工具路由结束

        # region 3. 静态上下文组装：仓库、Memory、Skill 与权限边界汇合
        assembled_context = self.context_assembler.build(
            ContextAssemblyRequest(
                task=session.task,
                workspace=self.config.workspace,
                working_memory=session.working_memory,
                tools=visible_tool_schemas,
                active_skill_cards=[
                    skill.prompt_card() for skill in session.active_skills
                ],
                max_chars=self.config.max_context_chars,
                permission_summary=model_permission_summary,
                instruction_target=self.config.instruction_target,
                global_instruction_files=tuple(self.config.global_instruction_files),
                runtime_instructions=self.config.runtime_instructions,
                instruction_max_bytes=max(
                    1,
                    int(self.config.instruction_max_bytes),
                ),
            )
        )
        # endregion 3. 静态上下文组装结束

        # region 4. 会话窗口治理：压缩历史并返回模型可直接消费的 PreparedTurn
        self._record_context_assembly(
            session=session,
            step=step,
            assembled_context=assembled_context,
            tool_route=tool_route,
            allowed_tool_names=allowed_tool_names,
        )
        system_context_message = Message("system", assembled_context.render())
        prepared_window = self.context_window.prepare(
            ContextWindowRequest(
                system_message=system_context_message,
                history=session.messages,
                observations=session.observations,
                tools=visible_tool_schemas,
                task=session.task,
                force_compaction=force_compaction,
            )
        )
        self._record_context_window(session, step, prepared_window)
        if prepared_window.digest is not None:
            session.lifecycle.update_checkpoint(
                TaskCheckpointUpdate(context_digest=prepared_window.digest.to_dict())
            )
        return PreparedTurn(
            step=step,
            context_message=system_context_message,
            messages_for_llm=prepared_window.messages,
            schemas=visible_tool_schemas,
            allowed_tool_names=allowed_tool_names,
            history_chars=sum(
                len(message.content or "")
                + len(str(message.tool_calls or ""))
                + len(message.reasoning_content or "")
                for message in session.messages
            ),
            tool_schema_chars=sum(len(str(schema)) for schema in visible_tool_schemas),
            estimated_prompt_tokens=prepared_window.estimated_tokens_after,
            compacted=prepared_window.compacted,
            session_digest=prepared_window.digest,
        )
        # endregion 4. 会话窗口治理结束

    # region 证据记录器（首次阅读可折叠）
    def _record_context_assembly(
        self,
        *,
        session: AgentRunSession,
        step: int,
        assembled_context: ContextReportView,
        tool_route: ToolRoute,
        allowed_tool_names: set[str],
    ) -> None:
        """记录上下文来源、裁剪和工具可见性，不保存完整 Prompt 正文。"""

        self.trace.add(
            step,
            session.agent_name,
            "context_assembly",
            context={
                "selected_files": assembled_context.selected_files,
                "retrieved_docs_count": len(assembled_context.retrieved_docs),
                "working_memory_summary": assembled_context.working_memory_summary,
                "total_chars": assembled_context.total_chars,
                "max_chars": assembled_context.max_chars,
                "truncated": assembled_context.truncated,
                "topic_relation": assembled_context.topic_relation,
                "inherit_session": assembled_context.inherit_session,
                "dropped_context": assembled_context.dropped_context,
                "budget_breakdown": assembled_context.budget_breakdown,
                "available_tools": assembled_context.available_tools,
                "active_skills": [
                    f"{skill.name}@{skill.version}" for skill in session.active_skills
                ],
                "permission_summary": assembled_context.permission_summary,
                "instructions": assembled_context.instruction_evidence,
                "tool_routing": {
                    "reason": tool_route.reason,
                    "allowed_tools": sorted(allowed_tool_names),
                    "dropped_tools": tool_route.dropped_names,
                    "metadata": tool_route.metadata,
                },
            },
        )

    def _record_context_window(
        self,
        session: AgentRunSession,
        step: int,
        prepared_window: ContextWindowResult,
    ) -> None:
        """记录压缩前后规模和硬上限，不复制完整消息数组。"""

        self.trace.add(
            step,
            session.agent_name,
            "context_window",
            context_window={
                "compacted": prepared_window.compacted,
                "reason": prepared_window.reason,
                "covered_message_count": prepared_window.covered_message_count,
                "estimated_tokens_before": prepared_window.estimated_tokens_before,
                "estimated_tokens_after": prepared_window.estimated_tokens_after,
                "hard_input_limit": prepared_window.hard_input_limit,
                "hard_limit_exceeded": (
                    prepared_window.estimated_tokens_after
                    > prepared_window.hard_input_limit
                ),
                "source_hash": (
                    prepared_window.digest.source_hash
                    if prepared_window.digest is not None
                    else ""
                ),
            },
        )

    # endregion 证据记录器结束

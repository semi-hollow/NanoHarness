"""一次模型 turn 的工具路由与上下文组装。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_forge.context.application import (
    ContextWindowManager,
    ContextWindowRequest,
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
    EnvironmentPort,
    EventSink,
    ToolGateway,
)
from agent_forge.tools.tool_router import ToolRouter, ToolRoutingRequest


@dataclass(frozen=True)
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
        self.context = context
        self.tools = tools
        self.environment = environment
        self.model_capabilities = model_capabilities
        self.tool_router = ToolRouter()
        effective_context_window = max(
            1_024,
            min(
                int(getattr(config, "max_prompt_tokens", 32_768)),
                model_capabilities.context_window,
            ),
        )
        self.context_window = ContextWindowManager(
            PromptBudget(
                max_prompt_tokens=effective_context_window,
                reserved_output_tokens=min(
                    max(0, int(getattr(config, "reserved_output_tokens", 4_096))),
                    effective_context_window - 512,
                ),
            )
        )

    # 主要入口：为当前 turn 路由工具、组装上下文并执行会话窗口治理。
    def execute(
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

        session.lifecycle.update(
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

        # region 工具路由准备（首遍可折叠）
        tool_route = self.tool_router.route(
            ToolRoutingRequest(
                task=session.task,
                schemas=self.tools.schemas(),
                step=step,
                agent_name=session.agent_name,
                skill_tool_names=session.skill_tool_names,
                mode=getattr(self.config, "tool_routing_mode", "task-aware"),
            )
        )
        visible_tool_schemas: list[ToolSchema] = tool_route.schemas
        allowed_tool_names = set(tool_route.allowed_names)
        model_permission_summary = (
            "read/list/grep allowed; write/apply_patch asks approval; "
            "dangerous commands denied; "
            f"{self.environment.describe()}"
        )
        if step == session.max_iterations:
            visible_tool_schemas = []
            allowed_tool_names = set()
            model_permission_summary += (
                "; final step: no more tool calls are available, provide the best "
                "evidence-based final answer and clearly mark unverified items"
            )
        # endregion 工具路由准备结束

        # region 上下文输入组装（首遍可折叠）
        assembled_context = self.context.build(
            ContextAssemblyRequest(
                task=session.task,
                workspace=self.config.workspace,
                working_memory=session.working_memory,
                tools=visible_tool_schemas,
                active_skill_cards=[
                    skill.prompt_card() for skill in session.active_skills
                ],
                max_chars=getattr(self.config, "max_context_chars", 8000),
                permission_summary=model_permission_summary,
                instruction_target=getattr(self.config, "instruction_target", ""),
                global_instruction_files=tuple(
                    getattr(self.config, "global_instruction_files", []) or []
                ),
                runtime_instructions=getattr(self.config, "runtime_instructions", ""),
                instruction_max_bytes=max(
                    1,
                    int(getattr(self.config, "instruction_max_bytes", 2_600)),
                ),
            )
        )
        # endregion 上下文输入组装结束
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
        if prepared_window.digest is not None:
            session.lifecycle.update(
                TaskCheckpointUpdate(
                    context_digest=prepared_window.digest.to_dict()
                )
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
            tool_schema_chars=sum(
                len(str(schema)) for schema in visible_tool_schemas
            ),
            estimated_prompt_tokens=prepared_window.estimated_tokens_after,
            compacted=prepared_window.compacted,
            session_digest=prepared_window.digest,
        )

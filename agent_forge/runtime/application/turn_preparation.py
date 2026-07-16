"""一次模型 turn 的工具路由与上下文组装。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_forge.context.application import ContextWindowManager, PromptBudget
from agent_forge.context.domain import SessionDigest
from agent_forge.contracts import ToolSchema
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.domain.task import TaskRunStatus
from agent_forge.runtime.ports import (
    ContextAssemblerPort,
    EnvironmentPort,
    EventSink,
    ToolGateway,
)
from agent_forge.tools.tool_router import ToolRouter


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
    ) -> None:
        self.config = config
        self.trace = trace
        self.context = context
        self.tools = tools
        self.environment = environment
        self.tool_router = ToolRouter()
        self.context_window = ContextWindowManager(
            PromptBudget(
                max_prompt_tokens=max(
                    512,
                    int(getattr(config, "max_prompt_tokens", 32_768)),
                ),
                reserved_output_tokens=max(
                    0,
                    int(getattr(config, "reserved_output_tokens", 4_096)),
                ),
            )
        )

    # 主要入口：下方定义承接该模块的核心调用。
    def execute(
        self,
        session: AgentRunSession,
        step: int,
        *,
        force_compaction: bool = False,
    ) -> PreparedTurn:
        """更新 checkpoint，路由工具，并生成当前 turn 的上下文。"""

        session.lifecycle.update(
            status=TaskRunStatus.RUNNING,
            current_step=step,
            messages_count=len(session.messages),
            observations_count=len(session.observations),
            resume_hint=(
                "Rerun with --resume-state to seed this task state into a continuation."
            ),
        )

        route = self.tool_router.route(
            session.task,
            self.tools.schemas(),
            step=step,
            agent_name=session.agent_name,
            skill_tool_names=session.skill_tool_names,
            mode=getattr(self.config, "tool_routing_mode", "task-aware"),
        )
        schemas: list[ToolSchema] = route.schemas
        allowed_tool_names = set(route.allowed_names)
        permission_summary = (
            "read/list/grep allowed; write/apply_patch asks approval; "
            "dangerous commands denied; "
            f"{self.environment.describe()}"
        )
        if step == session.max_iterations:
            schemas = []
            allowed_tool_names = set()
            permission_summary += (
                "; final step: no more tool calls are available, provide the best "
                "evidence-based final answer and clearly mark unverified items"
            )

        context_report = self.context.build(
            task=session.task,
            workspace=self.config.workspace,
            memory=session.memory,
            tools=schemas,
            active_skill_cards=[
                skill.prompt_card() for skill in session.active_skills
            ],
            max_chars=getattr(self.config, "max_context_chars", 8000),
            permission_summary=permission_summary,
        )
        self.trace.add(
            step,
            session.agent_name,
            "context_assembly",
            context={
                "selected_files": context_report.selected_files,
                "retrieved_docs_count": len(context_report.retrieved_docs),
                "memory_summary": context_report.memory_summary,
                "total_chars": context_report.total_chars,
                "max_chars": context_report.max_chars,
                "truncated": context_report.truncated,
                "topic_relation": context_report.topic_relation,
                "inherit_session": context_report.inherit_session,
                "dropped_context": context_report.dropped_context,
                "budget_breakdown": context_report.budget_breakdown,
                "available_tools": context_report.available_tools,
                "active_skills": [
                    f"{skill.name}@{skill.version}" for skill in session.active_skills
                ],
                "permission_summary": context_report.permission_summary,
                "tool_routing": {
                    "reason": route.reason,
                    "allowed_tools": sorted(allowed_tool_names),
                    "dropped_tools": route.dropped_names,
                    "metadata": route.metadata,
                },
            },
        )
        context_message = Message("system", context_report.render())
        window = self.context_window.prepare(
            system_message=context_message,
            history=session.messages,
            observations=session.observations,
            tools=schemas,
            task=session.task,
            force_compaction=force_compaction,
        )
        self.trace.add(
            step,
            session.agent_name,
            "context_window",
            context_window={
                "compacted": window.compacted,
                "reason": window.reason,
                "covered_message_count": window.covered_message_count,
                "estimated_tokens_before": window.estimated_tokens_before,
                "estimated_tokens_after": window.estimated_tokens_after,
                "hard_input_limit": window.hard_input_limit,
                "hard_limit_exceeded": (
                    window.estimated_tokens_after > window.hard_input_limit
                ),
                "source_hash": (
                    window.digest.source_hash if window.digest is not None else ""
                ),
            },
        )
        if window.digest is not None:
            session.lifecycle.update(context_digest=window.digest.to_dict())
        return PreparedTurn(
            step=step,
            context_message=context_message,
            messages_for_llm=window.messages,
            schemas=schemas,
            allowed_tool_names=allowed_tool_names,
            history_chars=sum(
                len(message.content or "")
                + len(str(message.tool_calls or ""))
                + len(message.reasoning_content or "")
                for message in session.messages
            ),
            tool_schema_chars=sum(len(str(schema)) for schema in schemas),
            estimated_prompt_tokens=window.estimated_tokens_after,
            compacted=window.compacted,
            session_digest=window.digest,
        )

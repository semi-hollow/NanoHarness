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
    phase: str


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
        # 先记录“即将准备第几轮”及当前消息计数；若后续 context/model 阶段异常，
        # resume 仍能从稳定的 turn 边界继续，而不是猜测执行到了哪里。
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
        # ToolRouter 同时返回 schema 和 allowed_names：前者发给模型，后者供执行时复核。
        # 两份视图来自同一 ToolRoute，避免模型看见的工具与 Runtime 放行集合不一致。
        registered_tool_schemas = self.tool_gateway.schemas()
        self._verify_skill_tool_dependencies(
            session=session,
            registered_tool_schemas=registered_tool_schemas,
        )
        tool_route = self.tool_router.route(
            ToolRoutingRequest(
                task=session.task,
                schemas=registered_tool_schemas,
                step=step,
                max_steps=session.max_iterations,
                agent_name=session.agent_name,
                skill_tool_names=session.skill_tool_names,
                mode=self.config.tool_routing_mode,
            )
        )
        visible_tool_schemas: list[ToolSchema] = tool_route.schemas
        allowed_tool_names = set(tool_route.allowed_names)
        model_permission_summary = (
            "read/list/search allowed; replace_text/write_file asks approval; "
            "dangerous commands denied; "
            f"{self.execution_environment.render_boundary_summary()}"
        )
        if tool_route.phase == "finalize":
            model_permission_summary += (
                "; final step: no more tool calls are available, provide the best "
                "evidence-based final answer and clearly mark unverified items"
            )
        elif tool_route.phase == "closeout":
            model_permission_summary += (
                "; closure phase: broad discovery is closed; use this last tool "
                "turn only to finish the smallest repair and inspect evidence"
            )
        # endregion 2. 工具路由结束

        # region 3. 静态上下文组装：仓库、Memory、Skill 与权限边界汇合
        # ContextAssembler 只拼装本轮稳定事实，不携带会话历史；历史压缩由下一阶段
        # ContextWindowManager 独立负责，避免仓库上下文和对话裁剪互相污染。
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
        # 先记录静态 context，再把 system context、历史和临时预算提示交给窗口管理器；
        # PreparedTurn 是 ModelPort 的唯一输入快照，后续不得再单独修改工具或消息。
        self._record_context_assembly(
            session=session,
            step=step,
            assembled_context=assembled_context,
            tool_route=tool_route,
            allowed_tool_names=allowed_tool_names,
        )
        system_context_message = Message(
            role="system",
            content=assembled_context.render(),
        )
        model_history = list(session.messages)
        runtime_control_message = self._turn_budget_control_message(
            step=step,
            max_steps=session.max_iterations,
        )
        if runtime_control_message is not None:
            model_history.append(runtime_control_message)
        prepared_window = self.context_window.prepare(
            ContextWindowRequest(
                system_message=system_context_message,
                history=model_history,
                observations=session.observations,
                tools=visible_tool_schemas,
                task=session.task,
                force_compaction=force_compaction,
            )
        )
        self._record_context_window(session, step, prepared_window)
        if prepared_window.digest is not None:
            # 只把摘要引用写入 checkpoint；原始消息仍保留在 session/trace 中，不被压缩删除。
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
            phase=tool_route.phase,
        )
        # endregion 4. 会话窗口治理结束

    @staticmethod
    def _verify_skill_tool_dependencies(
        *,
        session: AgentRunSession,
        registered_tool_schemas: list[ToolSchema],
    ) -> None:
        """在模型调用前拒绝依赖不完整的 Skill，避免只注入说明却无法执行。

        这类似 Spring 启动期 bean 依赖校验：Skill 可以推荐可选工具，但它声明的
        required tools 必须真实存在于当前 ToolGateway。这里仅校验“已注册”；写权限、
        审批、命令与路径安全仍由执行阶段独立判断，Skill 不能借声明绕过治理。
        """

        registered_tool_names = {
            str(schema.get("name") or "") for schema in registered_tool_schemas
        }
        for skill in session.active_skills:
            missing_tool_names = sorted(
                set(skill.required_tool_names) - registered_tool_names
            )
            if missing_tool_names:
                missing = ", ".join(missing_tool_names)
                raise ValueError(
                    f"activated skill {skill.name}@{skill.version} requires "
                    f"unavailable tools: {missing}"
                )

    @staticmethod
    def _turn_budget_control_message(
        *,
        step: int,
        max_steps: int,
    ) -> Message | None:
        """在接近预算边界时追加不落入会话历史的 Runtime 控制消息。"""

        remaining_tool_turns = max(0, max_steps - step)
        if remaining_tool_turns == 0:
            return Message(
                role="user",
                content=(
                    "[RUNTIME CONTROL] Tool execution is closed. Return the final "
                    "evidence-based answer now. Do not emit tool-call markup."
                ),
            )
        if remaining_tool_turns == 1:
            return Message(
                role="user",
                content=(
                    "[RUNTIME CONTROL] This is the last tool-enabled turn. Do not "
                    "start broad discovery. Finish the smallest repair, validation, "
                    "or diff check that can still change the outcome."
                ),
            )
        if remaining_tool_turns == 2:
            return Message(
                role="user",
                content=(
                    "[RUNTIME CONTROL] Closure phase has started. Stop broad "
                    "search and converge on the strongest supported repair."
                ),
            )
        return None

    # region 证据记录器
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
                    "phase": tool_route.phase,
                    "remaining_tool_turns": tool_route.remaining_tool_turns,
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

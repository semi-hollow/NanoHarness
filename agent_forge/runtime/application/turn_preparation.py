"""一次模型 turn 的工具路由与上下文组装。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from agent_forge.context.application import (
    PromptWindowManager,
    PromptWindowRequest,
    PromptWindowResult,
    PromptBudget,
)
from agent_forge.context.domain import ConversationHistoryDigest
from agent_forge.contracts import ToolSchema
from agent_forge.memory.domain import LongTermMemoryRecord
from agent_forge.memory.ports import LongTermMemoryRecallPort
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.domain.run_control import RUNTIME_COORDINATION_EVIDENCE_PREFIX
from agent_forge.runtime.domain.task import TaskCheckpointUpdate, TaskRunStatus
from agent_forge.runtime.ports import (
    TurnSystemContextAssemblerPort,
    TurnSystemContextRequest,
    TurnSystemContextView,
    EnvironmentPort,
    EventSink,
    ToolGateway,
)
from agent_forge.tools.tool_router import ToolRoute, ToolRouter, ToolRoutingRequest


@dataclass(frozen=True, kw_only=True)
class PreparedTurn:
    """一次 LLM 调用所需的完整、可度量输入。

    ``llm_messages`` 是当前 Turn System Context 与 Conversation Window；
    ``tool_schemas`` 是同一次调用的独立工具契约，不混进消息列表。
    """

    step: int
    turn_system_message: Message
    llm_messages: list[Message]
    tool_schemas: list[ToolSchema]
    allowed_tool_names: set[str]
    history_chars: int
    tool_schema_chars: int
    estimated_prompt_tokens: int
    compacted: bool
    conversation_history_digest: ConversationHistoryDigest | None
    phase: str


class TurnPreparation:
    """构造模型输入，但不调用模型也不执行工具。"""

    def __init__(
        self,
        config: RuntimeConfig,
        trace: EventSink,
        turn_system_context_assembler: TurnSystemContextAssemblerPort,
        tools: ToolGateway,
        environment: EnvironmentPort,
        model_capabilities: ModelCapabilities,
        long_term_memory_recall: LongTermMemoryRecallPort,
    ) -> None:
        self.config = config
        self.trace = trace
        self.turn_system_context_assembler = turn_system_context_assembler
        self.tool_gateway = tools
        self.execution_environment = environment
        self.model_capabilities = model_capabilities
        self.long_term_memory_recall = long_term_memory_recall
        self.tool_router = ToolRouter()
        effective_context_window = max(
            1_024,
            min(
                int(config.max_prompt_tokens),
                model_capabilities.context_window,
            ),
        )
        self.prompt_window = PromptWindowManager(
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

        伪代码：保存 Turn checkpoint -> 路由 Tool schema/allowed names
        -> 组装当前 Turn System Context -> 加入临时预算提示
        -> PromptWindow governance -> 返回冻结的 ``PreparedTurn``。

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
        memory_management_candidates = self._management_candidates_for_turn(
            session,
            step,
        )
        visible_tool_schemas = self._attach_memory_management_candidates(
            tool_route.schemas,
            memory_management_candidates,
        )
        allowed_tool_names = set(tool_route.allowed_names)
        model_permission_summary = (
            "read/list/search allowed; replace_text/write_file asks approval; "
            "remember_memory requires an exact explicit user quote; "
            "dangerous commands denied; "
            f"{self.execution_environment.render_boundary_summary()}"
        )
        # FINALIZE 关闭所有工具，明确要求模型只输出有证据约束的最终答案。
        if tool_route.phase == "finalize":
            model_permission_summary += (
                "; final step: no more tool calls are available, provide the best "
                "evidence-based final answer and clearly mark unverified items"
            )
        # CLOSEOUT 仍保留最后一次受限工具机会，但禁止重新开始宽泛探索。
        elif tool_route.phase == "closeout":
            model_permission_summary += (
                "; closure phase: broad discovery is closed; use this last tool "
                "turn only to finish the smallest repair and inspect evidence"
            )
        # endregion 2. 工具路由结束

        # region 3. 静态上下文组装：仓库、Memory、Skill 与权限边界汇合
        # ContextAssembler 只拼装本轮稳定事实，不携带会话历史；历史压缩由下一阶段
        # PromptWindowManager 独立负责，避免仓库上下文和对话裁剪互相污染。
        turn_system_context = self.turn_system_context_assembler.build(
            TurnSystemContextRequest(
                task=session.task,
                workspace=self.config.workspace,
                working_memory=session.working_memory,
                tool_schemas=visible_tool_schemas,
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
                system_prompt_profile=self.config.system_prompt_profile,
            )
        )
        # endregion 3. 静态上下文组装结束

        # region 4. 会话窗口治理：压缩历史并返回模型可直接消费的 PreparedTurn
        # 先记录静态 context，再把 system context、历史和临时预算提示交给窗口管理器；
        # PreparedTurn 是 ModelPort 的唯一输入快照，后续不得再单独修改工具或消息。
        self._record_turn_system_context(
            session=session,
            step=step,
            turn_system_context=turn_system_context,
            tool_route=tool_route,
            allowed_tool_names=allowed_tool_names,
        )
        turn_system_message = Message(
            role="system",
            content=turn_system_context.render(),
        )
        runtime_control_message = self._turn_budget_control_message(
            step=step,
            max_steps=session.max_iterations,
        )
        # 预算提示作为 transient tail 发送；它不进入 session.messages、digest 或 cursor。
        prompt_window = self.prompt_window.prepare(
            PromptWindowRequest(
                turn_system_message=turn_system_message,
                conversation_history=list(session.messages),
                observations=session.observations,
                tool_schemas=visible_tool_schemas,
                task=session.task,
                previous_digest=session.conversation_history_digest,
                compacted_message_cursor=session.compacted_message_cursor,
                transient_messages=(
                    (runtime_control_message,)
                    if runtime_control_message is not None
                    else ()
                ),
                force_compaction=force_compaction,
            )
        )
        self._record_prompt_window(session, step, prompt_window)
        # 更新同一 Session 的 rolling state；resume 只恢复 digest，新 Session cursor 归零。
        if prompt_window.conversation_history_digest is not None:
            session.conversation_history_digest = (
                prompt_window.conversation_history_digest
            )
            session.compacted_message_cursor = prompt_window.compacted_message_cursor
            session.lifecycle.update_checkpoint(
                TaskCheckpointUpdate(
                    conversation_history_digest=(
                        prompt_window.conversation_history_digest.to_dict()
                    )
                )
            )
        return PreparedTurn(
            step=step,
            turn_system_message=turn_system_message,
            llm_messages=prompt_window.llm_messages,
            tool_schemas=visible_tool_schemas,
            allowed_tool_names=allowed_tool_names,
            history_chars=sum(
                len(message.content or "")
                + len(str(message.tool_calls or ""))
                + len(message.reasoning_content or "")
                for message in session.messages
            ),
            tool_schema_chars=sum(len(str(schema)) for schema in visible_tool_schemas),
            estimated_prompt_tokens=prompt_window.estimated_tokens_after,
            compacted=prompt_window.compacted,
            conversation_history_digest=(prompt_window.conversation_history_digest),
            phase=tool_route.phase,
        )
        # endregion 4. 会话窗口治理结束

    def _management_candidates_for_turn(
        self,
        session: AgentRunSession,
        step: int,
    ) -> list[LongTermMemoryRecord]:
        """按最新 human message 查询一次，并把结果冻结到当前 Turn 结束。"""

        # Provider overflow 可能在同一个 step 再次准备请求；复用第一次候选，避免同一
        # Model response 的 schema 与后续 target validation 观察到两份 Repository 视图。
        if session.memory_management_candidates_step == step:
            return session.memory_management_candidates

        query, message_index = self._latest_human_authority_message(session)
        memory_namespace = self.config.memory_namespace or str(self.config.workspace)
        candidates = self.long_term_memory_recall.management_candidates(
            namespace=memory_namespace,
            query=query,
            max_chars=max(0, int(self.config.memory_max_chars)),
        )
        session.memory_management_candidates = candidates
        session.memory_management_candidates_step = step
        self._record_memory_management_candidates(
            session=session,
            step=step,
            namespace=memory_namespace,
            query=query,
            query_message_index=message_index,
            candidates=candidates,
        )
        return candidates

    def _record_memory_management_candidates(
        self,
        *,
        session: AgentRunSession,
        step: int,
        namespace: str,
        query: str,
        query_message_index: int,
        candidates: list[LongTermMemoryRecord],
    ) -> None:
        """记录当前 Turn 候选的来源与身份，不复制 Memory 正文。"""

        self.trace.add(
            step,
            session.agent_name,
            "memory_management_candidates",
            memory={
                "namespace": namespace,
                "query_message_index": query_message_index,
                "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                "candidate_count": len(candidates),
                "memory_ids": [record.memory_id for record in candidates],
            },
        )

    @staticmethod
    def _latest_human_authority_message(
        session: AgentRunSession,
    ) -> tuple[str, int]:
        """取最近 human-origin user 输入，排除 role=user 的 Runtime 协调编码。"""

        for message_index in range(len(session.messages) - 1, -1, -1):
            message = session.messages[message_index]
            if message.role != "user":
                continue
            if message.content.startswith(RUNTIME_COORDINATION_EVIDENCE_PREFIX):
                continue
            return message.content, message_index
        return session.task, -1

    @staticmethod
    def _attach_memory_management_candidates(
        schemas: list[ToolSchema],
        candidates: list[LongTermMemoryRecord],
    ) -> list[ToolSchema]:
        """只给 remember_memory schema 附加当前 Turn 冻结的 ID 候选。"""

        candidate_lines = [
            memory_record.render_management_line()
            for memory_record in candidates
        ]
        candidate_text = "\n".join(f"- {line}" for line in candidate_lines) or "- none"
        augmented_schemas: list[ToolSchema] = []
        for schema in schemas:
            if str(schema.get("name") or "") != "remember_memory":
                augmented_schemas.append(schema)
                continue
            augmented_schema = dict(schema)
            augmented_schema["description"] = (
                f"{schema.get('description', '')}\n"
                "Current-turn Memory Management Candidates (mutation targets only; "
                "not task reasoning evidence):\n"
                f"{candidate_text}\n"
                "Use CREATE when no candidate matches. UPDATE/NOOP must name one "
                "candidate target_memory_id with the requested scope."
            )
            augmented_schemas.append(augmented_schema)
        return augmented_schemas

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
        # 剩余为零表示 Tool 已关闭，本 Turn 必须只返回最终文本。
        if remaining_tool_turns == 0:
            return Message(
                role="user",
                content=(
                    "[RUNTIME CONTROL] Tool execution is closed. Return the final "
                    "evidence-based answer now. Do not emit tool-call markup."
                ),
            )
        # 剩余一次时只允许完成最小修复或验证，不再扩展问题范围。
        if remaining_tool_turns == 1:
            return Message(
                role="user",
                content=(
                    "[RUNTIME CONTROL] This is the last tool-enabled turn. Do not "
                    "start broad discovery. Finish the smallest repair, validation, "
                    "or diff check that can still change the outcome."
                ),
            )
        # 剩余两次时提前进入收口阶段，提示模型从探索切换到落地。
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
    def _record_turn_system_context(
        self,
        *,
        session: AgentRunSession,
        step: int,
        turn_system_context: TurnSystemContextView,
        tool_route: ToolRoute,
        allowed_tool_names: set[str],
    ) -> None:
        """记录上下文来源、裁剪和工具可见性，不保存完整 Prompt 正文。"""

        self.trace.add(
            step,
            session.agent_name,
            "context_assembly",
            context={
                "selected_files": turn_system_context.selected_files,
                "retrieved_docs_count": len(turn_system_context.retrieved_docs),
                "working_memory_summary": (turn_system_context.working_memory_summary),
                "total_chars": turn_system_context.total_chars,
                "max_chars": turn_system_context.max_chars,
                "truncated": turn_system_context.truncated,
                "topic_relation": turn_system_context.topic_relation,
                "inherit_session": turn_system_context.inherit_session,
                "dropped_context": turn_system_context.dropped_context,
                "budget_breakdown": turn_system_context.budget_breakdown,
                "available_tools": turn_system_context.available_tools,
                "active_skills": [
                    f"{skill.name}@{skill.version}" for skill in session.active_skills
                ],
                "permission_summary": turn_system_context.permission_summary,
                "system_prompt_profile": self.config.system_prompt_profile,
                "instructions": turn_system_context.instruction_evidence,
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

    def _record_prompt_window(
        self,
        session: AgentRunSession,
        step: int,
        prompt_window: PromptWindowResult,
    ) -> None:
        """记录压缩前后规模和硬上限，不复制完整消息数组。"""

        self.trace.add(
            step,
            session.agent_name,
            "context_window",
            context_window={
                "compacted": prompt_window.compacted,
                "reason": prompt_window.reason,
                "covered_message_count": prompt_window.covered_message_count,
                "current_session_cursor": prompt_window.compacted_message_cursor,
                "estimated_tokens_before": prompt_window.estimated_tokens_before,
                "estimated_tokens_after": prompt_window.estimated_tokens_after,
                "hard_input_limit": prompt_window.hard_input_limit,
                "hard_limit_exceeded": (
                    prompt_window.estimated_tokens_after
                    > prompt_window.hard_input_limit
                ),
                "source_hash": (
                    prompt_window.conversation_history_digest.source_hash
                    if prompt_window.conversation_history_digest is not None
                    else ""
                ),
            },
        )

    # endregion 证据记录器结束

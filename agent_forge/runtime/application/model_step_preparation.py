"""一次 Model Step 的工具路由与上下文组装。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

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
from agent_forge.runtime.application.session import (
    AgentRunSession,
    load_transaction_safe_conversation_page,
)
from agent_forge.runtime.application.context_budget import partition_context_budgets
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.domain.conversation import Observation
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.domain.thread import ConversationItem, ThreadContextState
from agent_forge.runtime.domain.task import TaskCheckpointUpdate, TaskRunStatus
from agent_forge.runtime.ports import (
    SystemContextAssemblerPort,
    ModelStepSystemContextRequest,
    ModelStepSystemContextView,
    EnvironmentPort,
    EventSink,
    ToolGateway,
)
from agent_forge.tools.tool_router import ToolRoute, ToolRouter, ToolRoutingRequest


@dataclass(frozen=True, kw_only=True)
class PreparedModelStep:
    """一次 LLM 调用所需的完整、可度量输入。

    ``llm_messages`` 是当前 Model Step System Context 与 Conversation Window；
    ``tool_schemas`` 是同一次调用的独立工具契约，不混进消息列表。
    """

    step: int
    model_step_system_message: Message
    llm_messages: list[Message]
    tool_schemas: list[ToolSchema]
    allowed_tool_names: set[str]
    history_chars: int
    tool_schema_chars: int
    estimated_prompt_tokens: int
    compacted: bool
    conversation_history_digest: ConversationHistoryDigest | None
    phase: str


class ModelStepPreparation:
    """构造模型输入，但不调用模型也不执行工具。"""

    def __init__(
        self,
        config: RuntimeConfig,
        trace: EventSink,
        system_context_assembler: SystemContextAssemblerPort,
        tools: ToolGateway,
        environment: EnvironmentPort,
        model_capabilities: ModelCapabilities,
        long_term_memory_recall: LongTermMemoryRecallPort,
    ) -> None:
        self.config = config
        self.trace = trace
        self.system_context_assembler = system_context_assembler
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

    # 主要入口：为当前 Model Step 路由工具、组装上下文并执行窗口治理。
    def prepare_model_step(
        self,
        session: AgentRunSession,
        step: int,
        *,
        force_compaction: bool = False,
    ) -> PreparedModelStep:
        """为 ``AgentLoop`` 生成一次可直接提交给模型的 ``PreparedModelStep``。

        伪代码：保存 Model Step checkpoint -> 路由 Tool schema/allowed names
        -> 组装当前 Model Step System Context -> 加入临时预算提示
        -> PromptWindow governance -> 返回冻结的 ``PreparedModelStep``。

        流程位置：每个 Model Step 的上下文、工具集合与预算汇合点。
        规范上游：``AgentLoop._run_model_step``。
        下一 owner：模型调用边界。
        状态与证据：RUNNING checkpoint、路由、裁剪与 token 预算事件。
        系统不变量：模型 schema 必须匹配 ``allowed_tool_names``，且压缩不能拆事务。
        删除/内联影响：会拆散模型请求的 context/tool/budget 一致性边界。
        """

        # region 1. Model Step 起点：先持久化恢复位置，再准备任何模型输入
        # 先记录“即将准备第几轮”及当前消息计数；若后续 context/model 阶段异常，
        # resume 仍能从稳定的 Model Step 边界继续，而不是猜测执行到了哪里。
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
        # endregion 1. Model Step 起点结束

        # region 2. Thread view 与工具路由：从 canonical ContextState 重建本次输入
        thread_context_state, previous_digest, has_more_uncovered = (
            self._refresh_thread_context_view(session)
        )
        # 收敛模型可见 schema，并保持权限摘要一致。
        # ToolRouter 同时返回 schema 和 allowed_names：前者发给模型，后者供执行时复核。
        # 两份视图来自同一 ToolRoute，避免模型看见的工具与 Runtime 放行集合不一致。
        registered_tool_schemas = [dict(schema) for schema in session.base_tool_schemas]
        tool_route = self.tool_router.route(
            ToolRoutingRequest(
                task=session.turn_focus,
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
                "step only to finish the smallest repair and inspect evidence"
            )
        # endregion 2. Thread view 与工具路由结束

        # region 3. 动态上下文组装：稳定前缀不重建，只刷新仓库与派生状态
        # SystemContextAssemblerPort 是动态仓库视图的唯一 IO owner；这里仅传入
        # 冻结前缀、最新 focus 和预算，避免 Application 主链自行扫描文件。
        _, dynamic_context_budget = partition_context_budgets(
            self.config.max_context_chars
        )
        model_step_system_context = self.system_context_assembler.build_model_step(
            ModelStepSystemContextRequest(
                turn_focus=session.turn_focus,
                stable_system_prefix=session.stable_system_prefix,
                workspace=self.config.workspace,
                working_memory=session.working_memory,
                tool_schemas=visible_tool_schemas,
                max_chars=dynamic_context_budget,
                permission_summary=model_permission_summary,
                frozen_instruction_paths=_frozen_instruction_paths(session),
            )
        )
        # endregion 3. 动态上下文组装结束

        # region 4. 会话窗口治理：压缩历史并返回模型可直接消费的 PreparedModelStep
        # 先记录静态 context，再把 system context、历史和临时预算提示交给窗口管理器；
        # PreparedModelStep 是 ModelPort 的唯一输入快照，后续不得再单独修改工具或消息。
        self._record_model_step_system_context(
            session=session,
            step=step,
            model_step_system_context=model_step_system_context,
            tool_route=tool_route,
            allowed_tool_names=allowed_tool_names,
        )
        model_step_system_message = Message(
            role="system",
            content=model_step_system_context.render(),
        )
        runtime_control_message = self._model_step_budget_control_message(
            step=step,
            max_steps=session.max_iterations,
        )
        # 历史超过单页时，先逐页做 deterministic rolling merge 并 CAS 推进 state。
        # 每次内存中最多保留一页；只有追到最终 protocol-preserving tail 后才构造请求，
        # 因而不会出现“模型看到旧 200 条，却遗漏最新 user Turn”的错误视图。
        while has_more_uncovered:
            page_window = self.prompt_window.prepare(
                PromptWindowRequest(
                    model_step_system_message=model_step_system_message,
                    conversation_history=list(session.messages),
                    observations=session.observations,
                    tool_schemas=visible_tool_schemas,
                    current_turn_id=session.turn_id,
                    current_turn_input_item_id=session.turn_input_item_id,
                    previous_digest=previous_digest,
                    force_compaction=True,
                )
            )
            if (
                page_window.conversation_history_digest is None
                or page_window.covered_delta_count <= 0
            ):
                raise RuntimeError(
                    "bounded Conversation page could not advance to the recent tail"
                )
            thread_context_state = self._save_thread_digest(
                session=session,
                state=thread_context_state,
                digest=page_window.conversation_history_digest,
                covered_delta_count=page_window.covered_delta_count,
            )
            thread_context_state, previous_digest, has_more_uncovered = (
                self._refresh_thread_context_view(session)
            )

        # 预算提示作为 transient tail 发送；它不进入 session.messages、digest 或 cursor。
        prompt_window = self.prompt_window.prepare(
            PromptWindowRequest(
                model_step_system_message=model_step_system_message,
                conversation_history=list(session.messages),
                observations=session.observations,
                tool_schemas=visible_tool_schemas,
                current_turn_id=session.turn_id,
                current_turn_input_item_id=session.turn_input_item_id,
                previous_digest=previous_digest,
                transient_messages=(
                    (runtime_control_message,)
                    if runtime_control_message is not None
                    else ()
                ),
                force_compaction=force_compaction,
            )
        )
        self._record_prompt_window(session, step, prompt_window)
        # Digest 只有 ThreadContextState 可以持久化；Session/Checkpoint 只保留 revision。
        if (
            prompt_window.conversation_history_digest is not None
            and prompt_window.covered_delta_count > 0
        ):
            thread_context_state = self._save_thread_digest(
                session=session,
                state=thread_context_state,
                digest=prompt_window.conversation_history_digest,
                covered_delta_count=prompt_window.covered_delta_count,
            )
        return PreparedModelStep(
            step=step,
            model_step_system_message=model_step_system_message,
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
            conversation_history_digest=prompt_window.conversation_history_digest,
            phase=tool_route.phase,
        )
        # endregion 4. 会话窗口治理结束

    def _refresh_thread_context_view(
        self,
        session: AgentRunSession,
    ) -> tuple[ThreadContextState, ConversationHistoryDigest | None, bool]:
        """从 Thread state 与 journal 重建有界 uncovered tail，并刷新 human focus。"""

        state = session.conversation_threads.load_context_state(session.thread_id)
        if state is None:
            state = ThreadContextState(thread_id=session.thread_id)
        if state.revision != session.context_revision:
            raise RuntimeError(
                "Thread context revision changed outside this Run; "
                f"session={session.context_revision}, actual={state.revision}"
            )
        digest = (
            ConversationHistoryDigest.from_dict(
                dict(state.conversation_history_digest)
            )
            if state.conversation_history_digest
            else None
        )
        items = load_transaction_safe_conversation_page(
            session.conversation_threads,
            thread_id=session.thread_id,
            after_sequence=state.covered_sequence,
            limit=200,
        )
        session.messages = [self._message_from_item(item) for item in items]
        session.observations = [
            self._observation_from_item(item) for item in items if item.role == "tool"
        ]
        session.message_sequences = [item.sequence for item in items]

        thread = session.conversation_threads.get(session.thread_id)
        if thread is None:
            raise KeyError(f"conversation thread not found: {session.thread_id}")
        recent_items = session.conversation_threads.list_items(
            session.thread_id,
            after_sequence=max(0, thread.sequence - 200),
            limit=200,
        )
        latest_human_item = next(
            (
                item
                for item in reversed(recent_items)
                if item.turn_id == session.turn_id and item.human_authority
            ),
            None,
        )
        if latest_human_item is not None:
            session.turn_focus = latest_human_item.content
            session.turn_focus_item_id = latest_human_item.item_id
        else:
            session.turn_focus = session.root_task
            session.turn_focus_item_id = ""

        # ask_human 的 provider-valid projection 会把授权 user item 移到 batch
        # 末尾，因此 projection 顺序不等于 journal sequence 顺序。
        last_loaded_sequence = max(
            session.message_sequences,
            default=state.covered_sequence,
        )
        return state, digest, thread.sequence > last_loaded_sequence

    def _save_thread_digest(
        self,
        *,
        session: AgentRunSession,
        state: ThreadContextState,
        digest: ConversationHistoryDigest,
        covered_delta_count: int,
    ) -> ThreadContextState:
        """CAS 推进 digest 与 covered sequence；Checkpoint 只更新 revision pointer。"""

        if not 0 < covered_delta_count <= len(session.message_sequences):
            raise ValueError("digest covered delta is outside bounded Conversation view")
        # Compaction 只在完整事务 segment 之后切分；用覆盖前缀的
        # 最大 raw sequence 推进 journal cursor，不被 ask_human 投影重排影响。
        covered_sequence = max(session.message_sequences[:covered_delta_count])
        candidate = replace(
            state,
            covered_sequence=covered_sequence,
            conversation_history_digest=digest.to_dict(),
        )
        try:
            saved = session.conversation_threads.save_context_state(
                candidate,
                expected_revision=state.revision,
            )
        except RuntimeError as exc:
            # 不做 last-write-wins：调用方必须重新从同一 revision 准备模型输入。
            raise RuntimeError(
                "Thread context CAS conflict; model input was not committed"
            ) from exc
        session.context_revision = saved.revision
        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(context_revision=saved.revision)
        )
        return saved

    @staticmethod
    def _message_from_item(item: ConversationItem) -> Message:
        return Message(
            role=item.role,
            content=item.content,
            name=item.name,
            tool_call_id=item.tool_call_id,
            tool_calls=[dict(call) for call in item.tool_calls] or None,
            reasoning_content=item.reasoning_content,
            origin=item.origin,
            human_authority=item.human_authority,
            item_id=item.item_id,
            turn_id=item.turn_id,
            human_input_request_id=str(
                item.metadata.get("human_input_request_id") or ""
            ),
        )

    @staticmethod
    def _observation_from_item(item: ConversationItem) -> Observation:
        return Observation(
            tool_name=str(item.metadata.get("tool_name") or item.name or "unknown"),
            success=bool(item.metadata.get("success", False)),
            content=item.content,
            execution_succeeded=(
                bool(item.metadata["execution_succeeded"])
                if item.metadata.get("execution_succeeded") is not None
                else None
            ),
            validation_status=(
                str(item.metadata["validation_status"])
                if item.metadata.get("validation_status") is not None
                else None
            ),
        )

    def _management_candidates_for_turn(
        self,
        session: AgentRunSession,
        step: int,
    ) -> list[LongTermMemoryRecord]:
        """只在 human focus 或成功 Memory mutation 变化后重查候选。"""

        # Provider overflow 在相同输入上重复准备时复用；steer/clarification 的 item id
        # 或成功 remember 后的显式 invalidation 会让 key 变化并重新查询。
        query = session.turn_focus
        candidate_key = hashlib.sha256(
            f"{session.turn_focus_item_id}\0{query}".encode("utf-8")
        ).hexdigest()
        if session.memory_management_candidates_key == candidate_key:
            return session.memory_management_candidates

        memory_namespace = self.config.memory_namespace or str(self.config.workspace)
        candidates = self.long_term_memory_recall.management_candidates(
            namespace=memory_namespace,
            query=query,
            max_chars=max(0, int(self.config.memory_max_chars)),
        )
        session.memory_management_candidates = candidates
        session.memory_management_candidates_key = candidate_key
        self._record_memory_management_candidates(
            session=session,
            step=step,
            namespace=memory_namespace,
            query=query,
            query_item_id=session.turn_focus_item_id,
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
        query_item_id: str,
        candidates: list[LongTermMemoryRecord],
    ) -> None:
        """记录当前 Turn 候选的来源与身份，不复制 Memory 正文。"""

        self.trace.add(
            step,
            session.agent_name,
            "memory_management_candidates",
            memory={
                "namespace": namespace,
                "query_item_id": query_item_id,
                "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                "candidate_count": len(candidates),
                "memory_ids": [record.memory_id for record in candidates],
            },
        )

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
    def _model_step_budget_control_message(
        *,
        step: int,
        max_steps: int,
    ) -> Message | None:
        """在接近预算边界时追加不落入会话历史的 Runtime 控制消息。"""

        remaining_tool_steps = max(0, max_steps - step)
        # 剩余为零表示 Tool 已关闭，本 Model Step 必须只返回最终文本。
        if remaining_tool_steps == 0:
            return Message(
                role="user",
                content=(
                    "[RUNTIME CONTROL] Tool execution is closed. Return the final "
                    "evidence-based answer now. Do not emit tool-call markup."
                ),
            )
        # 剩余一次时只允许完成最小修复或验证，不再扩展问题范围。
        if remaining_tool_steps == 1:
            return Message(
                role="user",
                content=(
                    "[RUNTIME CONTROL] This is the last tool-enabled model step. Do not "
                    "start broad discovery. Finish the smallest repair, validation, "
                    "or diff check that can still change the outcome."
                ),
            )
        # 剩余两次时提前进入收口阶段，提示模型从探索切换到落地。
        if remaining_tool_steps == 2:
            return Message(
                role="user",
                content=(
                    "[RUNTIME CONTROL] Closure phase has started. Stop broad "
                    "search and converge on the strongest supported repair."
                ),
            )
        return None

    # region 证据记录器
    def _record_model_step_system_context(
        self,
        *,
        session: AgentRunSession,
        step: int,
        model_step_system_context: ModelStepSystemContextView,
        tool_route: ToolRoute,
        allowed_tool_names: set[str],
    ) -> None:
        """记录上下文来源、裁剪和工具可见性，不保存完整 Prompt 正文。"""

        raw_active_skills = session.stable_context_evidence.get("active_skills")
        active_skill_items = (
            raw_active_skills if isinstance(raw_active_skills, list) else []
        )
        self.trace.add(
            step,
            session.agent_name,
            "context_assembly",
            context={
                "selected_files": model_step_system_context.selected_files,
                "retrieved_docs_count": len(model_step_system_context.retrieved_docs),
                "working_memory_summary": (model_step_system_context.working_memory_summary),
                "total_chars": model_step_system_context.total_chars,
                "max_chars": model_step_system_context.max_chars,
                "truncated": model_step_system_context.truncated,
                "stable_chars": model_step_system_context.stable_chars,
                "dynamic_chars": model_step_system_context.dynamic_chars,
                "dynamic_max_chars": model_step_system_context.dynamic_max_chars,
                "dropped_context": model_step_system_context.dropped_context,
                "budget_breakdown": model_step_system_context.budget_breakdown,
                "available_tools": model_step_system_context.available_tools,
                "active_skills": [
                    str(item.get("name") or "")
                    for item in active_skill_items
                    if isinstance(item, dict)
                ],
                "permission_summary": model_step_system_context.permission_summary,
                "system_prompt_profile": self.config.system_prompt_profile,
                "instructions": session.stable_context_evidence.get(
                    "instructions",
                    {},
                ),
                "tool_routing": {
                    "reason": tool_route.reason,
                    "phase": tool_route.phase,
                    # 历史 trace key 保持不变；值的运行时语义是一项 Model Step 预算。
                    "remaining_tool_turns": tool_route.remaining_tool_model_steps,
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
                "covered_delta_count": prompt_window.covered_delta_count,
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


def _frozen_instruction_paths(session: AgentRunSession) -> tuple[str, ...]:
    """返回本 Turn 已冻结的 governing instruction source 路径。"""

    raw_instructions = session.stable_context_evidence.get("instructions")
    if not isinstance(raw_instructions, dict):
        return ()
    raw_sources = raw_instructions.get("sources")
    if not isinstance(raw_sources, list):
        return ()
    return tuple(
        path
        for source in raw_sources
        if isinstance(source, dict)
        if (path := str(source.get("path") or ""))
    )

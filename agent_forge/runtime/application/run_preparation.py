"""Single Agent 首次模型调用前的 Run/Turn 上下文准备。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from agent_forge.memory.domain import LongTermMemoryRecord
from agent_forge.contracts import JsonObject, ToolSchema
from agent_forge.runtime.application.clarification import (
    ClarificationDecision,
    ClarificationPolicy,
)
from agent_forge.runtime.application.context_budget import partition_context_budgets
from agent_forge.runtime.application.dependencies import RuntimeDependencies
from agent_forge.runtime.application.run_lifecycle import RunLifecycle, StopRequest
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.application.step_control import StepController
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import Message, Observation
from agent_forge.runtime.domain.human_input import HumanInputQuestion
from agent_forge.runtime.domain.task import (
    TaskCheckpoint,
    TaskCheckpointUpdate,
    TaskRunStatus,
)
from agent_forge.runtime.domain.thread import (
    ConversationItem,
    ConversationItemDraft,
    StableTurnContextSnapshot,
)
from agent_forge.runtime.ports import SkillView, StableTurnContextRequest
from agent_forge.safety.guardrails import GuardrailResult, input_guardrail


CONVERSATION_VIEW_LIMIT = 200
TURN_CONTEXT_CONTRACT_REVISION = 1


class RunPreparation:
    """创建 Run session，并冻结或恢复当前 Turn 的稳定输入快照。"""

    def __init__(
        self, config: RuntimeConfig, dependencies: RuntimeDependencies
    ) -> None:
        self.config = config
        self.trace = dependencies.events
        self.environment = dependencies.environment
        self.task_states = dependencies.task_states
        self.conversation_threads = dependencies.conversation_threads
        self.human_inputs = dependencies.human_inputs
        self.hooks = dependencies.hooks
        self.memory_recall = dependencies.long_term_memory_recall
        self.clarification_policy = ClarificationPolicy()
        self.skill_selector = dependencies.skills
        self.tool_gateway = dependencies.tools
        self.context_assembler = dependencies.system_context_assembler
        self.model_capabilities = dependencies.model_capabilities

    def create_session(self, agent_name: str) -> AgentRunSession:
        """从 Thread/Turn 读取 root task，并创建本 attempt 的最小 checkpoint。

        流程位置：``Harness`` 完成装配后，AgentLoop 进入治理主链的第一站。
        规范上游：``AgentLoop.run``。
        下一 owner：``prepare_run``，随后才允许进入 Model Step。
        状态与证据：Run checkpoint、Thread 有界视图、StableTurnContextSnapshot 与模型能力事件。
        系统不变量：任务身份只来自 canonical Thread/Turn，checkpoint 不能复制另一份任务真相。
        删除/内联影响：会把身份校验、恢复装载和 session 组装重新散到 AgentLoop。
        """

        # region 1. 权威身份：Task 只从当前 Turn 读取，checkpoint/调用参数不再复制
        # RuntimeConfig 只携带 Harness 已经选定的 identity；真正的 root task 必须回到
        # Thread repository 读取。这样 resume 参数或旧 checkpoint 不能偷偷改写用户目标。
        if not self.config.thread_id or not self.config.turn_id:
            raise ValueError("RuntimeConfig requires canonical thread_id and turn_id")
        thread = self.conversation_threads.get(self.config.thread_id)
        if thread is None:
            raise KeyError(f"conversation thread not found: {self.config.thread_id}")
        turn = thread.require_turn(self.config.turn_id)
        root_task = turn.root_task

        # config.context_revision 是 Harness 规划/claim 这个 Run 时已经看见的版本；
        # durable_context_revision 是此刻磁盘真正可恢复的 ContextState 版本。
        # 磁盘版本更大是合法的：例如新 Turn snapshot 在 claim 时完成了一次 CAS；
        # 反过来 config 更大则表示 checkpoint 声称引用了一个磁盘上不存在的未来版本。
        context_state = self.conversation_threads.load_context_state(thread.thread_id)
        durable_context_revision = (
            context_state.revision if context_state is not None else 0
        )
        if self.config.context_revision > durable_context_revision:
            raise ValueError(
                "checkpoint references a context revision that is not durable: "
                f"checkpoint={self.config.context_revision}, "
                f"actual={durable_context_revision}"
            )

        # turn_focus 是本次检索/Tool routing 使用的最新 human-authority 输入；
        # 找不到后续 clarification 时才回退到不可变的 Turn.root_task。
        turn_focus, turn_focus_item_id = self._derive_turn_focus(
            thread.sequence, turn.turn_id
        )
        # endregion 1. 权威身份结束

        # region 2. Attempt checkpoint：加载 claim 前已 durable 的 CREATED bootstrap
        # Harness 在进入 AgentLoop 前已经做完两步：
        # 1) 写一个 status=CREATED 的 TaskCheckpoint；
        # 2) 把同一 run_id 发布为 Turn.current_run_id（这一步叫 claim）。
        # create_session 不负责抢占 Run；它只证明这两个 durable 事实指向同一个 Attempt，
        # 然后才把状态从 CREATED 推进到 RUNNING。
        self.trace.set_run_context(task=root_task)
        initial_checkpoint = self.task_states.load_path(
            str(self.task_states.path_for(self.trace.run_id))
        )
        # 校验 identity/workspace/agent/context contract，防止读取到别的 Run 的 bootstrap。
        self._validate_bootstrap_checkpoint(
            initial_checkpoint,
            thread_id=thread.thread_id,
            turn_id=turn.turn_id,
            agent_name=agent_name,
        )

        # Fresh Run 没有 resume_state；Resume Run 则从“上一 Attempt 的 resumable
        # checkpoint”复制 execution cursor。新 Run 仍保留自己的 run_id 和 bootstrap。
        restored_checkpoint = self._load_resume_checkpoint()
        update = TaskCheckpointUpdate(
            status=TaskRunStatus.RUNNING,
            context_revision=durable_context_revision,
            metadata={
                **initial_checkpoint.metadata,
                "execution_environment": self.environment.probe().to_dict(),
                "model_capabilities": self.model_capabilities.to_dict(),
            },
        )
        if restored_checkpoint is not None:
            # Resume 只允许续跑同一个 Thread/Turn；跨 Turn 复制 pending ToolCall 会把
            # 旧任务的副作用带进新任务，因此 identity 不一致时直接拒绝。
            if (
                restored_checkpoint.thread_id != thread.thread_id
                or restored_checkpoint.turn_id != turn.turn_id
            ):
                raise ValueError(
                    "resume checkpoint does not belong to configured Thread/Turn"
                )
            update = replace(
                update,
                current_step=restored_checkpoint.current_step,
                last_tool=restored_checkpoint.last_tool,
                last_observation=restored_checkpoint.last_observation,
                pending_execution=restored_checkpoint.pending_execution,
            )
            self._record_resume_state_loaded(
                agent_name=agent_name,
                checkpoint=restored_checkpoint,
            )

        # 这次 update 是 TaskCheckpoint 的 CREATED -> RUNNING 状态转换。
        # 直到它 durable 之后，后面的 ThreadRun navigation 才同步显示 running。
        running_checkpoint = self.task_states.update(initial_checkpoint, update)

        # turn.runs 是 Thread 的 Run 导航表。next(..., None) 只是在其中寻找 Harness
        # 已经 claim 的本 run_id；它不会创建或抢占 Run。找不到说明 checkpoint 虽存在，
        # 但 Thread 从未承认这个 Attempt；current_run_id 不同则说明它已被别的 Resume 取代。
        claimed_thread_run = next(
            (item for item in turn.runs if item.run_id == self.trace.run_id),
            None,
        )
        if claimed_thread_run is None or turn.current_run_id != self.trace.run_id:
            raise RuntimeError("Run bootstrap is not the current claimed ThreadRun")

        # TaskCheckpoint 保存可恢复执行状态；ThreadRun 只保存导航状态。这里把同一个
        # running 事实镜像到导航表，但不把 messages/pending cursor 复制进 Thread。
        self.conversation_threads.record_run(
            thread.thread_id,
            turn.turn_id,
            replace(
                claimed_thread_run,
                status=TaskRunStatus.RUNNING.value,
                current_step=running_checkpoint.current_step,
                updated_at=running_checkpoint.updated_at,
            ),
        )

        # Trace/Hook 只消费已经 durable 的 running checkpoint；不会先记录一个尚未提交的状态。
        self.trace.record_task_state_checkpoint(
            step=running_checkpoint.current_step,
            agent_name=agent_name,
            checkpoint=running_checkpoint,
        )
        self.hooks.on_checkpoint(running_checkpoint)
        self._record_model_capabilities(agent_name)

        # RunLifecycle 从此成为该 Run checkpoint 的唯一 transition owner；后续等待、
        # pause、failure 和 completed 都通过它提交，而不是由 AgentLoop 直接写 JSON。
        lifecycle = RunLifecycle(
            checkpoint=running_checkpoint,
            task_state_store=self.task_states,
            conversation_threads=self.conversation_threads,
            thread_id=thread.thread_id,
            turn_id=turn.turn_id,
            human_input_store=self.human_inputs,
            workspace=self.config.workspace,
            trace=self.trace,
            hooks=self.hooks,
        )
        # endregion 2. Attempt checkpoint 结束

        # region 3. 有界视图：只加载 digest 尚未覆盖的 raw Conversation tail
        # covered_sequence 之前的内容已经存在 digest 中，再加载会让模型同时看到
        # “摘要 + 原文”两份历史。首次 Session 因此只装入 cursor 之后的 journal delta。
        messages, observations, sequences = self._load_conversation_view(
            thread_id=thread.thread_id,
            after_sequence=(
                context_state.covered_sequence if context_state is not None else 0
            ),
        )
        # AgentRunSession 是本进程的有界工作视图；durable owner 仍分别是 Turn、
        # ContextState 和 TaskCheckpoint。这里组装对象，不产生新的权威事实。
        session = AgentRunSession(
            thread_id=thread.thread_id,
            turn_id=turn.turn_id,
            turn_input_item_id=turn.input_item_id,
            root_task=root_task,
            turn_focus=turn_focus,
            turn_focus_item_id=turn_focus_item_id,
            agent_name=agent_name,
            workspace_root=self.config.workspace,
            max_iterations=self.config.max_steps,
            lifecycle=lifecycle,
            controller=StepController.from_config(self.config),
            conversation_threads=self.conversation_threads,
            context_revision=durable_context_revision,
            messages=messages,
            observations=observations,
            message_sequences=sequences,
        )
        # Turn 一进入 Runtime 就冻结稳定契约；即使操作员在首个模型步骤前 pause，
        # 后续 resume 也只复用这里的 Snapshot，不会读取已经变化的治理规则。
        self._ensure_stable_turn_context_snapshot(session)
        return session
        # endregion 3. 有界视图结束

    def _validate_bootstrap_checkpoint(
        self,
        checkpoint: TaskCheckpoint,
        *,
        thread_id: str,
        turn_id: str,
        agent_name: str,
    ) -> None:
        """证明 RunPreparation 正在推进 claim 时验证过的同一个 bootstrap。"""

        # 第一层：checkpoint 必须属于当前 Trace/Thread/Turn，不能拿另一个 Run 的
        # CREATED 文件作为本 Attempt 的起点。
        if (
            checkpoint.run_id != self.trace.run_id
            or checkpoint.thread_id != thread_id
            or checkpoint.turn_id != turn_id
        ):
            raise ValueError("bootstrap checkpoint identity does not match current Run")

        # 第二层：只有 CREATED 能进入 create_session。RUNNING/terminal 表示这个
        # bootstrap 已经被消费过，再次启动会制造两个执行者。
        if checkpoint.status != TaskRunStatus.CREATED.value:
            raise ValueError("RunPreparation requires a CREATED bootstrap checkpoint")

        # 第三层：物理 workspace、execution mode、agent 和 Context revision 必须与
        # Harness 装配时的 RuntimeConfig 完全一致，避免恢复到另一套执行环境。
        requested_workspace = Path(
            self.config.requested_workspace or self.config.workspace
        ).resolve()
        if (
            Path(checkpoint.workspace).resolve() != requested_workspace
            or Path(checkpoint.execution_workspace).resolve()
            != Path(self.config.workspace).resolve()
            or checkpoint.execution_mode != self.config.execution_mode
            or checkpoint.agent_name != agent_name
            or checkpoint.context_revision != self.config.context_revision
        ):
            raise ValueError(
                "bootstrap checkpoint contract does not match RuntimeConfig"
            )

    def prepare_run(self, session: AgentRunSession) -> StopRequest | None:
        """在已冻结 Turn snapshot 上执行输入治理与澄清，不重复发现稳定输入。

        流程位置：Run session 已创建、任何 Model Step 尚未开始的准备门。
        规范上游：``AgentLoop.run``。
        下一 owner：``ModelStepPreparation.prepare_model_step``，或一个明确的 StopRequest。
        状态与证据：输入 Guardrail、已冻结的 Skill/Memory 选择与人工澄清。
        系统不变量：同一 Turn 的 resume 必须复用稳定快照，新 Run 不得静默重读治理规则。
        删除/内联影响：会让输入治理、稳定上下文和澄清顺序失去唯一 owner。
        """

        # 顺序是 contract：先检查 canonical root task 是否允许处理，再判断是否缺少
        # 信息。被 policy 拒绝的任务不会通过 clarification 重新包装后继续执行。
        input_policy_stop = self._apply_input_policy(session)
        if input_policy_stop is not None:
            return input_policy_stop

        # Clarification 可能立即得到已持久化的人类回答，也可能返回 waiting_human。
        # 后一种情况必须在任何 Model Step 前停止，避免模型基于不完整输入先行动作。
        clarification_stop = self._resolve_clarification(session)
        if clarification_stop is not None:
            return clarification_stop

        # clarification 可能向 journal 新增 authoritative item；进入模型前重读一次
        # bounded view，确保 Session 与 durable Conversation 对齐。
        self._refresh_conversation_view(session)
        return None

    # region Turn 稳定快照
    def _ensure_stable_turn_context_snapshot(self, session: AgentRunSession) -> None:
        # region 1. 复用：同一 Turn 只接受已经 durable 的 immutable Snapshot
        # Snapshot 的 owner 是 ThreadContextState，而不是当前 Run。先查 durable state，
        # 可以保证 fresh execution 与 same-Turn resume 都使用同一份治理输入。
        existing = self.conversation_threads.load_stable_turn_snapshot(
            session.thread_id,
            session.turn_id,
        )
        if existing is not None:
            # _restore_snapshot 会先校验 hash、Turn identity、Runtime contract 与 Tool
            # schemas；验证通过后才把 immutable snapshot 投影到可变 Session。
            self._restore_snapshot(session, existing)
            if not self.config.resume_state:
                # Fresh Run 记录本次看到的稳定选择；Resume 已有原 Attempt 的 selection
                # evidence，这里只记录 snapshot reused，避免把“重新选择”写进 Trace。
                self._record_skill_selection(session, existing)
                self._record_memory_recall(
                    session,
                    self.config.memory_namespace or str(self.config.workspace),
                    session.long_term_memory_snapshot,
                )
            self._record_snapshot(session, existing, reused=True)
            return

        # 正常 Harness 路径会在 Run claim 前创建 snapshot。这个分支主要服务测试或
        # embedded caller 直接装配 AgentLoop 的情况；即便如此，resume 也不能重新生成，
        # 否则同一个 Turn 会因为指令/Skill/Memory 已变化而得到另一套运行规则。
        if self.config.resume_state:
            raise RuntimeError(
                "cannot resume Turn without durable StableTurnContextSnapshot"
            )
        # endregion 1. Snapshot 复用与 fail-closed结束

        # region 2. 新 Turn 冻结：Direct AgentLoop 的兼容 fallback
        # 这里只构造 immutable value，还没有改变 ContextState；真正发布发生在下一段 CAS。
        snapshot = self.build_stable_turn_context_snapshot(
            turn_id=session.turn_id,
            root_task=session.root_task,
        )
        # endregion 2. 新 Turn 稳定输入冻结结束

        # region 3. CAS 持久化：先保存 Snapshot，再把 revision 与投影恢复到当前 Session
        # expected_revision 防止覆盖另一个进程刚完成的 compaction/snapshot 更新。
        # CAS 成功会令 ContextState.revision + 1；新 revision 随后同步到 Session 和
        # TaskCheckpoint，三者才共同指向同一份 durable ContextState。
        saved_state = self.conversation_threads.save_stable_turn_snapshot(
            session.thread_id,
            snapshot,
            expected_revision=session.context_revision,
        )
        session.context_revision = saved_state.revision
        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(context_revision=saved_state.revision)
        )
        self._restore_snapshot(session, snapshot)
        self._record_skill_selection(session, snapshot)
        self._record_memory_recall(
            session,
            self.config.memory_namespace or str(self.config.workspace),
            session.long_term_memory_snapshot,
        )
        self._record_snapshot(session, snapshot, reused=False)
        # endregion 3. Snapshot 持久化与 Session 恢复结束

    def build_stable_turn_context_snapshot(
        self,
        *,
        turn_id: str,
        root_task: str,
    ) -> StableTurnContextSnapshot:
        """发现并构造新 Turn 的稳定输入；调用方负责在 claim 前持久化。"""

        # region 1. Capability inputs：先确定本 Turn 可用的 Skill 与基础 Tool contract
        # Skill selection 只基于 canonical root task。Tool schemas 取完整 registry 快照；
        # 后续每个 Model Step 的 ToolRouter 只能收窄，不能临时扩大这个 capability 上限。
        active_skills = self._select_active_skills(root_task)
        base_tool_schemas = [dict(schema) for schema in self.tool_gateway.schemas()]
        # Skill 声明的 required tools 若不在 registry 中，当前 Turn 从一开始就不可执行；
        # 在 freeze 时失败比运行到一半才发现缺工具更容易恢复和解释。
        self._verify_skill_tool_dependencies(active_skills, base_tool_schemas)
        # endregion 1. Capability inputs 结束

        # region 2. Memory 与预算：把跨 Turn recall 冻结为本 Turn 的只读输入
        memory_namespace = self.config.memory_namespace or str(self.config.workspace)
        recalled_memories = self.memory_recall.recall(
            namespace=memory_namespace,
            query=root_task,
            max_chars=max(0, int(self.config.memory_max_chars)),
        )
        # max_context_chars 被稳定区与动态区分开。这里只消费 stable budget；repository
        # map/file previews 的 dynamic budget 留给每个 Model Step 重新构造。
        stable_budget, _ = partition_context_budgets(self.config.max_context_chars)
        # endregion 2. Memory 与预算结束

        # region 3. Stable prefix：一次性解析 System Prompt、Instructions、Skill 与 Memory
        # freeze_stable 是稳定 Context 的唯一 assembler；RunPreparation 只提供 typed input，
        # 不在主链里自行扫描 AGENTS.md 或拼接 Prompt，从而保持单一构造规则。
        stable_context = self.context_assembler.freeze_stable(
            StableTurnContextRequest(
                root_task=root_task,
                workspace=self.config.workspace,
                base_tool_schemas=base_tool_schemas,
                active_skill_cards=[skill.prompt_card() for skill in active_skills],
                long_term_memory=[
                    record.render_prompt_line() for record in recalled_memories
                ],
                max_chars=stable_budget,
                instruction_target=self.config.instruction_target,
                global_instruction_files=tuple(self.config.global_instruction_files),
                runtime_instructions=self.config.runtime_instructions,
                instruction_max_bytes=max(1, int(self.config.instruction_max_bytes)),
                system_prompt_profile=self.config.system_prompt_profile,
            )
        )
        # endregion 3. Stable prefix 结束

        # region 4. Evidence 与 immutable snapshot：正文和验证证据一起冻结
        # stable_evidence 不复制 raw Conversation；它记录本次选择、裁剪与兼容性 contract，
        # 供 resume 判断“当前 Runtime 是否仍能安全执行这份旧 snapshot”。
        stable_evidence: JsonObject = {
            "budget_breakdown": dict(stable_context.budget_breakdown),
            "total_chars": stable_context.total_chars,
            "max_chars": stable_context.max_chars,
            "truncated": stable_context.truncated,
            "dropped_context": list(stable_context.dropped_context),
            "instructions": dict(stable_context.instruction_evidence),
            "active_skills": [self._skill_evidence(skill) for skill in active_skills],
            "available_tools": list(stable_context.available_tools),
            "runtime_contract": self._runtime_contract(),
        }
        # normalized() 计算 contract_hash。后续持久化/恢复会重算并比对，任何字段漂移
        # 都会在模型调用前失败，而不是静默接受被部分改写的 snapshot。
        snapshot = StableTurnContextSnapshot(
            turn_id=turn_id,
            root_task=root_task,
            stable_system_prefix=stable_context.render(),
            base_tool_schemas=tuple(dict(schema) for schema in base_tool_schemas),
            skill_tool_names=tuple(
                sorted({name for skill in active_skills for name in skill.tool_names})
            ),
            long_term_memory_snapshot=tuple(
                record.to_dict() for record in recalled_memories
            ),
            stable_context_evidence=stable_evidence,
        ).normalized()
        return snapshot
        # endregion 4. Evidence 与 immutable snapshot 结束

    def validate_snapshot_contract(
        self,
        snapshot: StableTurnContextSnapshot,
        *,
        turn_id: str,
        root_task: str,
    ) -> StableTurnContextSnapshot:
        """在 resume claim 前纯校验冻结输入与当前 Runtime/Tool 契约。"""

        # normalized() 首先验证 snapshot 自身 contract_hash，证明持久化正文未漂移。
        normalized = snapshot.normalized()
        # Snapshot 只能服务创建它的 Turn/root task；相同文本也不能跨 Turn 复用 identity。
        if normalized.turn_id != turn_id or normalized.root_task != root_task:
            raise ValueError("Turn snapshot identity does not match authoritative Turn")

        # Runtime contract 包含 model capabilities、Prompt profile 和预算。Resume 若换了
        # 这些规则，相当于在同一 Turn 中途换执行协议，因此选择 fail closed。
        raw_runtime_contract = normalized.stable_context_evidence.get(
            "runtime_contract"
        )
        if not isinstance(raw_runtime_contract, dict):
            raise ValueError("Turn snapshot is missing its Runtime context contract")
        if _canonical_json(raw_runtime_contract) != _canonical_json(
            self._runtime_contract()
        ):
            raise ValueError("current Runtime is incompatible with frozen Turn context")

        # Tool schemas 也必须完全一致。ToolRouter 可以在每步收窄可见工具，但 resume
        # 不能用新的 registry 扩大或改变原 Turn 冻结的 capability contract。
        current_schemas = [dict(schema) for schema in self.tool_gateway.schemas()]
        if _canonical_json(current_schemas) != _canonical_json(
            list(normalized.base_tool_schemas)
        ):
            raise ValueError(
                "current Tool registry no longer matches frozen Turn schemas"
            )
        return normalized

    def _restore_snapshot(
        self,
        session: AgentRunSession,
        snapshot: StableTurnContextSnapshot,
    ) -> None:
        # 先做全部 fail-closed validation，再写 Session；校验失败时不会留下“恢复一半”
        # 的 stable prefix/tools/memory 组合。
        normalized = self.validate_snapshot_contract(
            snapshot,
            turn_id=session.turn_id,
            root_task=session.root_task,
        )
        # 下列字段只是 immutable snapshot 在本 Run 的内存投影。Session 可以重建，
        # ThreadContextState 中的 snapshot 才是 same-Turn resume 的 durable owner。
        session.stable_system_prefix = normalized.stable_system_prefix
        session.base_tool_schemas = [
            dict(schema) for schema in normalized.base_tool_schemas
        ]
        session.skill_tool_names = set(normalized.skill_tool_names)
        session.stable_context_evidence = dict(normalized.stable_context_evidence)
        session.long_term_memory_snapshot = [
            LongTermMemoryRecord.from_dict(dict(item))
            for item in normalized.long_term_memory_snapshot
        ]
        session.active_skills = []

    # endregion Turn 稳定快照

    # region 输入与 clarification
    def _apply_input_policy(self, session: AgentRunSession) -> StopRequest | None:
        decision = input_guardrail(session.root_task)
        self._record_input_guardrail(session, decision)
        if decision.passed:
            return None
        return StopRequest(
            status=TaskRunStatus.BLOCKED,
            reason="input_guardrail_block",
            stop_output=f"blocked: {decision.reason}",
        )

    def _resolve_clarification(self, session: AgentRunSession) -> StopRequest | None:
        decision = self.clarification_policy.evaluate_task(session.root_task)
        self._record_clarification_decision(session, decision)
        if decision.action == "refuse":
            return StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason="unsupported_task",
                stop_output=f"blocked: {decision.reason}",
            )
        if not decision.needs_user_input():
            return None
        resolution = session.lifecycle.request_human_input(
            HumanInputQuestion(
                agent_name=session.agent_name,
                kind="clarification",
                question=decision.question,
                choices=(),
                reason=decision.reason,
                step=0,
            )
        )
        if resolution.stop is not None:
            return resolution.stop

        # Human answer 是当前 Turn 的新 focus；它不能重写 root_task 或稳定快照。
        focus_content = "\n".join(
            [
                "Resolved operator clarification:",
                f"Question: {resolution.request.question}",
                f"Answer: {resolution.request.answer}",
            ]
        )
        item = self.conversation_threads.append(
            session.thread_id,
            ConversationItemDraft(
                item_id=f"human-input:{resolution.request.request_id}",
                turn_id=session.turn_id,
                run_id=self.trace.run_id,
                role="user",
                content=focus_content,
                origin="operator",
                human_authority=True,
                metadata={"human_input_request_id": resolution.request.request_id},
            ),
        )
        session.turn_focus = item.content
        session.turn_focus_item_id = item.item_id
        session.memory_management_candidates_key = ""
        self._record_human_input_response_loaded(session, resolution.request.to_dict())
        return None

    # endregion 输入与 clarification

    # region 有界 Conversation 视图
    def _refresh_conversation_view(self, session: AgentRunSession) -> None:
        # ContextState 同时拥有 revision 与 covered_sequence。refresh 必须从同一份 state
        # 读取二者，不能一个来自旧 Session、一个来自新磁盘版本。
        state = self.conversation_threads.load_context_state(session.thread_id)
        after_sequence = state.covered_sequence if state is not None else 0
        session.context_revision = state.revision if state is not None else 0

        # 只重建尚未进入 digest 的 journal tail。messages/observations/sequences 是三个
        # 对齐视图：模型协议、typed Tool 结果、以及持久化 cursor 所需的 raw sequence。
        messages, observations, sequences = self._load_conversation_view(
            thread_id=session.thread_id,
            after_sequence=after_sequence,
        )
        session.messages = messages
        session.observations = observations
        session.message_sequences = sequences
        thread = self.conversation_threads.get(session.thread_id)
        if thread is None:
            raise KeyError(f"conversation thread not found: {session.thread_id}")

        # 人工 clarification 可能刚刚 append；再次倒序扫描当前 Turn，令下一 Model Step
        # 的 retrieval/Tool routing 使用最新 focus，同时保持 root_task 不变。
        focus, focus_item_id = self._derive_turn_focus(thread.sequence, session.turn_id)
        session.turn_focus = focus
        session.turn_focus_item_id = focus_item_id

    def _load_conversation_view(
        self,
        *,
        thread_id: str,
        after_sequence: int,
    ) -> tuple[list[Message], list[Observation], list[int]]:
        # Repository 返回 canonical ConversationItem；下面只做内存投影，不改变 journal。
        # limit 控制一次 Session 的 catch-up 页大小，更多数据由 ModelStepPreparation 分页追赶。
        items = self.conversation_threads.list_items(
            thread_id,
            after_sequence=after_sequence,
            limit=CONVERSATION_VIEW_LIMIT,
        )
        return (
            [self._message_from_item(item) for item in items],
            [
                self._observation_from_item(item)
                for item in items
                if item.role == "tool"
            ],
            [item.sequence for item in items],
        )

    def _derive_turn_focus(self, thread_sequence: int, turn_id: str) -> tuple[str, str]:
        # 从 journal 尾部倒序找当前 Turn 最新 human-authority item。倒序意味着第一条
        # 命中就是当前有效 focus；runtime-generated role=user 因 human_authority=false
        # 会被跳过。
        recent_items = self.conversation_threads.list_items(
            self.config.thread_id,
            after_sequence=max(0, thread_sequence - CONVERSATION_VIEW_LIMIT),
            limit=CONVERSATION_VIEW_LIMIT,
        )
        for item in reversed(recent_items):
            if item.turn_id == turn_id and item.human_authority:
                return item.content, item.item_id

        # 极端情况下当前 Turn 的输入不在这一个 bounded page 内，仍从 Turn domain
        # 取 canonical root_task，而不是把空字符串或旧 Turn 消息当作 focus。
        thread = self.conversation_threads.get(self.config.thread_id)
        if thread is None:
            raise KeyError(f"conversation thread not found: {self.config.thread_id}")
        return thread.require_turn(turn_id).root_task, ""

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
        metadata = item.metadata
        return Observation(
            tool_name=str(metadata.get("tool_name") or item.name or "unknown"),
            success=bool(metadata.get("success", False)),
            content=item.content,
            execution_succeeded=(
                bool(metadata["execution_succeeded"])
                if metadata.get("execution_succeeded") is not None
                else None
            ),
            validation_status=(
                str(metadata["validation_status"])
                if metadata.get("validation_status") is not None
                else None
            ),
        )

    # endregion 有界 Conversation 视图

    def _load_resume_checkpoint(self) -> TaskCheckpoint | None:
        return (
            self.task_states.load_path(self.config.resume_state)
            if self.config.resume_state
            else None
        )

    def _select_active_skills(self, root_task: str) -> list[SkillView]:
        if self.config.skill_mode == "none":
            return []
        requested_names = list(self.config.skill_names)
        return list(
            self.skill_selector.select_for_task(
                root_task,
                names=requested_names or None,
                limit=1,
            )
        )

    @staticmethod
    def _verify_skill_tool_dependencies(
        active_skills: list[SkillView],
        schemas: list[ToolSchema],
    ) -> None:
        registered = {str(schema.get("name") or "") for schema in schemas}
        for skill in active_skills:
            missing = sorted(set(skill.required_tool_names) - registered)
            if missing:
                raise ValueError(
                    f"activated skill {skill.name}@{skill.version} requires "
                    f"unavailable tools: {', '.join(missing)}"
                )

    @staticmethod
    def _skill_evidence(skill: SkillView) -> JsonObject:
        return {
            "name": skill.name,
            "version": skill.version,
            "required_tools": list(skill.required_tool_names),
            "optional_tools": list(skill.optional_tool_names),
            "tools": list(skill.tool_names),
            "entrypoint": skill.entrypoint,
            "source": skill.source,
            "content_sha256": skill.content_sha256,
            "selection_reason": skill.selection_reason,
        }

    def _runtime_contract(self) -> JsonObject:
        """返回 resume 必须精确兼容的最小 Runtime/Prompt 边界。"""

        stable_budget, dynamic_budget = partition_context_budgets(
            self.config.max_context_chars
        )
        return {
            "revision": TURN_CONTEXT_CONTRACT_REVISION,
            "model_capabilities": self.model_capabilities.to_dict(),
            "system_prompt_profile": self.config.system_prompt_profile,
            "stable_context_chars": stable_budget,
            "dynamic_context_chars": dynamic_budget,
            "max_prompt_tokens": int(self.config.max_prompt_tokens),
            "reserved_output_tokens": int(self.config.reserved_output_tokens),
        }

    # region 证据记录器
    def _record_model_capabilities(self, agent_name: str) -> None:
        self.trace.add(
            0,
            agent_name,
            "model_capabilities",
            model_capabilities=self.model_capabilities.to_dict(),
        )

    def _record_resume_state_loaded(
        self,
        *,
        agent_name: str,
        checkpoint: TaskCheckpoint,
    ) -> None:
        """记录本 Run 从哪个 execution pointer 续跑，不复制 Thread 内容。"""

        self.trace.add(
            checkpoint.current_step,
            agent_name,
            "resume_state_loaded",
            resume_state=self.config.resume_state,
            resume={
                "thread_id": checkpoint.thread_id,
                "turn_id": checkpoint.turn_id,
                "previous_run_id": checkpoint.run_id,
                "current_step": checkpoint.current_step,
                "pending_execution": (
                    checkpoint.pending_execution.to_dict()
                    if checkpoint.pending_execution is not None
                    else None
                ),
            },
        )

    def _record_input_guardrail(
        self,
        session: AgentRunSession,
        decision: GuardrailResult,
    ) -> None:
        self.trace.add(
            0,
            session.agent_name,
            "guardrail_check",
            guardrail={
                "category": decision.category,
                "passed": decision.passed,
                "reason": decision.reason,
                "severity": decision.severity,
            },
        )

    def _record_clarification_decision(
        self,
        session: AgentRunSession,
        decision: ClarificationDecision,
    ) -> None:
        self.trace.add(
            0,
            session.agent_name,
            "clarification_decision",
            clarification={
                "action": decision.action,
                "confidence": decision.confidence,
                "reason": decision.reason,
                "question": decision.question,
                "missing_fields": decision.missing_fields,
            },
        )

    def _record_human_input_response_loaded(
        self,
        session: AgentRunSession,
        request: JsonObject,
    ) -> None:
        """记录人工澄清已回填到当前 Turn，不在主编排里展开 Trace 字段。"""

        self.trace.add(
            0,
            session.agent_name,
            "human_input_response_loaded",
            request=request,
        )

    def _record_skill_selection(
        self,
        session: AgentRunSession,
        snapshot: StableTurnContextSnapshot,
    ) -> None:
        raw_skills = snapshot.stable_context_evidence.get("active_skills")
        skills = list(raw_skills) if isinstance(raw_skills, list) else []
        self.trace.add(
            0,
            session.agent_name,
            "skill_selection",
            skills=skills,
            skill_mode=self.config.skill_mode,
            snapshot_contract_hash=snapshot.contract_hash,
        )

    def _record_memory_recall(
        self,
        session: AgentRunSession,
        namespace: str,
        memories: list[LongTermMemoryRecord],
    ) -> None:
        payload = [record.to_dict() for record in memories]
        self.trace.add(
            0,
            session.agent_name,
            "memory_recall",
            memory={
                "namespace": namespace,
                "recalled_count": len(memories),
                "memory_ids": [record.memory_id for record in memories],
                "revisions": [record.revision for record in memories],
                "snapshot_sha256": hashlib.sha256(
                    _canonical_json(payload).encode("utf-8")
                ).hexdigest(),
            },
        )

    def _record_snapshot(
        self,
        session: AgentRunSession,
        snapshot: StableTurnContextSnapshot,
        *,
        reused: bool,
    ) -> None:
        self.trace.add(
            0,
            session.agent_name,
            "context_assembly",
            context={
                "thread_id": session.thread_id,
                "turn_id": session.turn_id,
                "context_revision": session.context_revision,
                "contract_hash": snapshot.contract_hash,
                "reused": reused,
                "instructions": snapshot.stable_context_evidence.get(
                    "instructions",
                    {},
                ),
                "stable_context": snapshot.stable_context_evidence,
            },
        )

    # endregion 证据记录器


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

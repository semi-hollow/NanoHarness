"""Single Agent 运行前的会话创建与前置决策。"""

from __future__ import annotations

import hashlib
import json

from agent_forge.context.domain import LongTermMemoryRecord
from agent_forge.runtime.application.dependencies import RuntimeDependencies
from agent_forge.runtime.application.run_lifecycle import RunLifecycle, StopRequest
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.clarification import ClarificationDecision, ClarificationPolicy
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.control import StepController
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.domain.human_input import HumanInputQuestion
from agent_forge.runtime.domain.task import (
    TaskCheckpointData,
    TaskRunStatus,
    TaskStartRequest,
    summarize_checkpoint,
)
from agent_forge.runtime.ports import SkillView
from agent_forge.safety.guardrails import GuardrailResult, input_guardrail


class RunPreparation:
    """创建 run，并在首次模型调用前完成所有一次性决策。

    阅读入口只有两个：``create_session`` 创建显式会话，``prepare_run`` 完成
    guardrail、clarification/人工恢复、Skill 选择与长期记忆召回。
    """

    def __init__(
        self,
        config: RuntimeConfig,
        dependencies: RuntimeDependencies,
        *,
        human_thread_id: str,
    ) -> None:
        self.config = config
        self.trace = dependencies.events
        self.environment = dependencies.environment
        self.task_states = dependencies.task_states
        self.human_inputs = dependencies.human_inputs
        self.hooks = dependencies.hooks
        self.memory_recall = dependencies.long_term_memory_recall
        self.human_thread_id = human_thread_id
        self.clarification_policy = ClarificationPolicy()
        self.skill_selector = dependencies.skills
        self.model_capabilities = dependencies.model_capabilities

    # 主要入口：创建本次 run 的 session、lifecycle 和首个 durable checkpoint。
    def create_session(self, task: str, agent_name: str) -> AgentRunSession:
        """把 ``AgentLoop.run`` 的规范输入转换为可恢复的运行会话。

        流程位置：黄金主链的 session 与首个 durable state 创建点。
        规范上游：``AgentLoop.run``。
        下一 owner：``RunLifecycle`` 与 ``AgentRunSession``。
        状态与证据：首个 checkpoint、环境和模型能力事件。
        系统不变量：任何 turn 开始前都已有唯一 run id 和首个状态事实。
        删除/内联影响：会失去 turn 前 durable-state 屏障并扩大 ``AgentLoop``。
        """

        # region 准备区（首遍可折叠）：恢复摘要与首个 durable checkpoint
        self.trace.set_run_context(task=task)
        restored_state_summary = self._load_resume_summary(agent_name)
        initial_checkpoint = self.task_states.start(
            TaskStartRequest(
                run_id=self.trace.run_id,
                task=task,
                workspace=self.config.workspace,
                agent_name=agent_name,
                metadata={
                    "execution_environment": self.environment.probe().to_dict(),
                    "human_thread_id": self.human_thread_id,
                    "model_capabilities": self.model_capabilities.to_dict(),
                },
            )
        )
        self.trace.record_task_state_checkpoint(
            step=0,
            agent_name=agent_name,
            checkpoint=initial_checkpoint,
        )
        self.hooks.on_checkpoint(initial_checkpoint)
        self._record_model_capabilities(agent_name)
        run_lifecycle = RunLifecycle(
            checkpoint=initial_checkpoint,
            task_state_store=self.task_states,
            human_input_store=self.human_inputs,
            human_thread_id=self.human_thread_id,
            workspace=self.config.workspace,
            trace=self.trace,
            hooks=self.hooks,
        )
        # endregion 会话准备结束
        return AgentRunSession(
            task=task,
            agent_name=agent_name,
            workspace_root=self.config.workspace,
            max_iterations=self.config.max_steps,
            lifecycle=run_lifecycle,
            controller=StepController.from_config(self.config),
            resume_summary=restored_state_summary,
        )

    # 主要入口：应用输入策略、恢复人工状态、选择 Skill 并召回长期记忆。
    def prepare_run(self, session: AgentRunSession) -> StopRequest | None:
        """完成首次模型调用前的一次性决策，并把控制权还给 ``AgentLoop``。

        流程位置：首次模型调用之前的一次性策略阶段。
        规范上游：``AgentLoop.run``。
        下一 owner：成功时 ``TurnPreparation.prepare_turn``；停止时
        ``RunLifecycle.finalize_run``。
        状态与证据：guardrail、clarification、Skill 与 memory 决定写入 trace。
        系统不变量：本方法只返回 ``StopRequest``，不直接写终态。
        删除/内联影响：会把一次性策略重新散入 turn loop。
        """

        input_policy_stop = self._apply_input_policy(session)
        if input_policy_stop is not None:
            return input_policy_stop
        clarification_stop = self._resolve_clarification(session)
        if clarification_stop is not None:
            return clarification_stop
        self._activate_skills(session)
        self._initialize_memory_context(session)
        return None

    # region 一次性准备规则（首次阅读可折叠）
    def _apply_input_policy(self, session: AgentRunSession) -> StopRequest | None:
        guardrail_decision = input_guardrail(session.task)
        self._record_input_guardrail(session, guardrail_decision)
        if guardrail_decision.passed:
            return None
        return StopRequest(
            status=TaskRunStatus.BLOCKED,
            reason="input_guardrail_block",
            final_answer=f"blocked: {guardrail_decision.reason}",
        )

    def _resolve_clarification(
        self,
        session: AgentRunSession,
    ) -> StopRequest | None:
        clarification_decision = self.clarification_policy.evaluate_task(session.task)
        self._record_clarification_decision(session, clarification_decision)
        if clarification_decision.action == "refuse":
            return StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason="unsupported_task",
                final_answer=f"blocked: {clarification_decision.reason}",
            )
        if not clarification_decision.needs_user_input():
            return None

        human_input_resolution = session.lifecycle.request_human_input(
            HumanInputQuestion(
                agent_name=session.agent_name,
                kind="clarification",
                question=clarification_decision.question,
                choices=(),
                reason=clarification_decision.reason,
                step=0,
            )
        )
        if human_input_resolution.stop is not None:
            return human_input_resolution.stop
        session.task = "\n".join(
            [
                session.task,
                "",
                "Resolved operator clarification:",
                f"Question: {human_input_resolution.request.question}",
                f"Answer: {human_input_resolution.request.answer}",
                "Continue from this answer and do not ask the same question again.",
            ]
        )
        self._record_human_response_loaded(
            session,
            human_input_resolution.request.to_dict(),
        )
        return None

    def _activate_skills(self, session: AgentRunSession) -> None:
        session.active_skills = self._select_active_skills(session.task)
        session.skill_tool_names = {
            tool_name
            for skill in session.active_skills
            for tool_name in skill.tool_names
        }
        self._record_skill_selection(session)

    def _initialize_memory_context(self, session: AgentRunSession) -> None:
        """创建 working memory，并在 Run 开始时固定长期记忆快照。

        这里只读一次 Repository。后续每个 Turn 都复用
        ``WorkingMemory.long_term_records``，因此运行中 remember/forget 不会
        悄悄改变已启动 Run 的判断依据。
        """

        session.messages = [Message(role="user", content=session.task)]
        prior_session_summary = self.config.session_summary
        if session.resume_summary:
            prior_session_summary = "\n".join(
                part for part in [prior_session_summary, session.resume_summary] if part
            )
        session.working_memory.seed_session(
            previous_task=self.config.previous_task,
            session_summary=prior_session_summary,
        )
        memory_namespace = self.config.memory_namespace or str(self.config.workspace)
        recalled_memories = self.memory_recall.recall(
            namespace=memory_namespace,
            limit=max(0, int(self.config.memory_recall_limit)),
        )
        session.working_memory.seed_long_term(recalled_memories)
        session.working_memory.set("task", session.task)
        self._record_memory_recall(
            session=session,
            memory_namespace=memory_namespace,
            recalled_memories=recalled_memories,
        )

    def _load_resume_summary(self, agent_name: str) -> str:
        resume_checkpoint_path = self.config.resume_state
        if not resume_checkpoint_path:
            return ""
        restored_checkpoint = self.task_states.load_path(resume_checkpoint_path)
        restored_summary = summarize_checkpoint(restored_checkpoint)
        self._record_resume_state_loaded(
            agent_name=agent_name,
            resume_checkpoint_path=resume_checkpoint_path,
            checkpoint=restored_checkpoint.to_dict(),
            resume_summary=restored_summary,
        )
        return restored_summary

    def _select_active_skills(self, task: str) -> list[SkillView]:
        skill_selection_mode = self.config.skill_mode
        if skill_selection_mode == "none":
            return []
        explicitly_requested_skill_names = list(self.config.skill_names)
        return list(
            self.skill_selector.select_for_task(
                task,
                names=explicitly_requested_skill_names or None,
                # 自动模式只选一个主工作流。多个重叠 Skill 会重复指令并挤占任务证据；
                # 需要组合时由调用方通过 skill_names 显式声明。
                limit=1,
            )
        )

    # region 证据记录器（首次阅读可折叠）
    def _record_model_capabilities(self, agent_name: str) -> None:
        """记录本次运行实际采用的模型能力边界。"""

        self.trace.add(
            0,
            agent_name,
            "model_capabilities",
            model_capabilities=self.model_capabilities.to_dict(),
        )

    def _record_input_guardrail(
        self,
        session: AgentRunSession,
        decision: GuardrailResult,
    ) -> None:
        """记录输入文本风险提示；它不是最终安全授权。"""

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
        """记录继续、追问或拒绝及其判断依据。"""

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

    def _record_human_response_loaded(
        self,
        session: AgentRunSession,
        human_input_request_data: dict,
    ) -> None:
        """记录 continuation 已取得对应人工回答。"""

        self.trace.add(
            0,
            session.agent_name,
            "human_input_response_loaded",
            request=human_input_request_data,
        )

    def _record_skill_selection(self, session: AgentRunSession) -> None:
        """记录 Skill 选择结果和它扩展的工具集合。"""

        self.trace.add(
            0,
            session.agent_name,
            "skill_selection",
            skills=[
                {
                    "name": skill.name,
                    "version": skill.version,
                    "tools": skill.tool_names,
                    "entrypoint": skill.entrypoint,
                    "source": getattr(skill, "source", skill.entrypoint),
                    "prompt_chars": len(skill.prompt_card()),
                }
                for skill in session.active_skills
            ],
            skill_mode=self.config.skill_mode,
            disclosure="metadata discovery -> selected full prompt card",
        )

    def _record_memory_recall(
        self,
        *,
        session: AgentRunSession,
        memory_namespace: str,
        recalled_memories: list[LongTermMemoryRecord],
    ) -> None:
        """记录本 Run 固定快照的身份和指纹，不复制记忆正文。"""

        snapshot_payload = [
            memory_record.to_dict() for memory_record in recalled_memories
        ]
        snapshot_sha256 = hashlib.sha256(
            json.dumps(
                snapshot_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        self.trace.add(
            0,
            session.agent_name,
            "memory_recall",
            memory={
                "namespace": memory_namespace,
                "recalled_count": len(recalled_memories),
                "memory_ids": [
                    memory_record.memory_id for memory_record in recalled_memories
                ],
                "keys": [memory_record.key for memory_record in recalled_memories],
                "revisions": [
                    memory_record.revision for memory_record in recalled_memories
                ],
                "scopes": [
                    memory_record.scope for memory_record in recalled_memories
                ],
                "snapshot_sha256": snapshot_sha256,
            },
        )

    def _record_resume_state_loaded(
        self,
        *,
        agent_name: str,
        resume_checkpoint_path: str,
        checkpoint: TaskCheckpointData,
        resume_summary: str,
    ) -> None:
        """记录 continuation 的来源 checkpoint 和注入模型的摘要。"""

        self.trace.add(
            0,
            agent_name,
            "resume_state_loaded",
            resume_state=resume_checkpoint_path,
            checkpoint=checkpoint,
            resume_summary=resume_summary,
        )

    # endregion 证据记录器结束

    # endregion 一次性准备规则结束

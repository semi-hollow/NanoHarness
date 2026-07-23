"""Single Agent 运行前的会话创建与前置决策。"""

from __future__ import annotations

from agent_forge.runtime.application.dependencies import RuntimeDependencies
from agent_forge.runtime.application.run_lifecycle import RunLifecycle, StopRequest
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.clarification import ClarificationPolicy
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.control import StepController
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.domain.human_input import HumanInputQuestion
from agent_forge.runtime.domain.task import (
    TaskRunStatus,
    TaskStartRequest,
    summarize_checkpoint,
)
from agent_forge.runtime.ports import SkillView
from agent_forge.safety.guardrails import input_guardrail


class RunPreparation:
    """创建 run，并在首次模型调用前完成所有一次性决策。

    阅读入口只有两个：``start`` 创建显式会话，``execute`` 完成 guardrail、
    clarification/人工恢复、Skill 选择与长期记忆召回。
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
    def start(self, task: str, agent_name: str) -> AgentRunSession:
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
        self.trace.add(
            0,
            agent_name,
            "model_capabilities",
            model_capabilities=self.model_capabilities.to_dict(),
        )
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
    def execute(self, session: AgentRunSession) -> StopRequest | None:
        """完成首次模型调用前的一次性决策，并把控制权还给 ``AgentLoop``。

        流程位置：首次模型调用之前的一次性策略阶段。
        规范上游：``AgentLoop.run``。
        下一 owner：成功时 ``TurnPreparation.execute``；停止时 ``RunLifecycle.stop``。
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
        self.trace.add(
            0,
            session.agent_name,
            "guardrail_check",
            guardrail={
                "category": guardrail_decision.category,
                "passed": guardrail_decision.passed,
                "reason": guardrail_decision.reason,
                "severity": guardrail_decision.severity,
            },
        )
        if guardrail_decision.passed:
            return None
        return StopRequest(
            TaskRunStatus.BLOCKED,
            "input_guardrail_block",
            f"blocked: {guardrail_decision.reason}",
        )

    def _resolve_clarification(
        self,
        session: AgentRunSession,
    ) -> StopRequest | None:
        clarification_decision = self.clarification_policy.decide(session.task)
        self.trace.add(
            0,
            session.agent_name,
            "clarification_decision",
            clarification={
                "action": clarification_decision.action,
                "confidence": clarification_decision.confidence,
                "reason": clarification_decision.reason,
                "question": clarification_decision.question,
                "missing_fields": clarification_decision.missing_fields,
            },
        )
        if clarification_decision.action == "refuse":
            return StopRequest(
                TaskRunStatus.BLOCKED,
                "unsupported_task",
                f"blocked: {clarification_decision.reason}",
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
        self.trace.add(
            0,
            session.agent_name,
            "human_input_response_loaded",
            request=human_input_resolution.request.to_dict(),
        )
        return None

    def _activate_skills(self, session: AgentRunSession) -> None:
        session.active_skills = self._select_active_skills(session.task)
        session.skill_tool_names = {
            tool_name
            for skill in session.active_skills
            for tool_name in skill.tool_names
        }
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
            skill_mode=getattr(self.config, "skill_mode", "auto"),
            disclosure="metadata discovery -> selected full prompt card",
        )

    def _initialize_memory_context(self, session: AgentRunSession) -> None:
        """创建 working memory，并注入经过权威与隔离过滤的长期召回结果。"""

        session.messages = [Message("user", session.task)]
        prior_session_summary = getattr(self.config, "session_summary", "")
        if session.resume_summary:
            prior_session_summary = "\n".join(
                part
                for part in [prior_session_summary, session.resume_summary]
                if part
            )
        session.working_memory.seed_session(
            previous_task=getattr(self.config, "previous_task", ""),
            session_summary=prior_session_summary,
        )
        memory_namespace = getattr(self.config, "memory_namespace", "") or str(
            self.config.workspace
        )
        recalled_memories = self.memory_recall.recall(
            session.task,
            namespace=memory_namespace,
            agent_name=session.agent_name,
            limit=max(0, int(getattr(self.config, "memory_recall_limit", 6))),
        )
        session.working_memory.seed_long_term(recalled_memories)
        session.working_memory.set("task", session.task)
        self.trace.add(
            0,
            session.agent_name,
            "memory_recall",
            memory={
                "namespace": memory_namespace,
                "recalled_count": len(recalled_memories),
                "memory_ids": [item.memory_id for item in recalled_memories],
                "kinds": [item.kind for item in recalled_memories],
            },
        )

    def _load_resume_summary(self, agent_name: str) -> str:
        resume_checkpoint_path = getattr(self.config, "resume_state", "")
        if not resume_checkpoint_path:
            return ""
        restored_checkpoint = self.task_states.load_path(resume_checkpoint_path)
        restored_summary = summarize_checkpoint(restored_checkpoint)
        self.trace.add(
            0,
            agent_name,
            "resume_state_loaded",
            resume_state=resume_checkpoint_path,
            checkpoint=restored_checkpoint.to_dict(),
            resume_summary=restored_summary,
        )
        return restored_summary

    def _select_active_skills(self, task: str) -> list[SkillView]:
        skill_selection_mode = getattr(self.config, "skill_mode", "auto")
        if skill_selection_mode == "none":
            return []
        explicitly_requested_skill_names = list(
            getattr(self.config, "skill_names", []) or []
        )
        return list(
            self.skill_selector.select_for_task(
                task,
                names=explicitly_requested_skill_names or None,
                limit=3,
            )
        )
    # endregion 一次性准备规则结束

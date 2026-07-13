"""Single Agent 的阶段编排。

首次阅读只展开 ``AgentLoop.run``。工具分支见 ``tool_execution.py``，暂停和停止见
``run_lifecycle.py``，一次运行的数据字段见 ``state.py``。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_forge.context.context_builder import build_context_report
from agent_forge.context.repo_map import build_repo_map
from agent_forge.contracts import ToolSchema
from agent_forge.observability.trace import TraceRecorder
from agent_forge.runtime.approval import ApprovalStore
from agent_forge.runtime.clarification import ClarificationPolicy
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.control import StepController
from agent_forge.runtime.execution_environment import (
    ExecutionEnvironment,
    ExecutionEnvironmentConfig,
)
from agent_forge.runtime.hooks import HookManager
from agent_forge.runtime.human_input import HumanInputStore
from agent_forge.runtime.llm_client import AgentResponse, LLMClient
from agent_forge.runtime.message import Message
from agent_forge.runtime.operation_ledger import OperationLedgerStore
from agent_forge.runtime.planner import SimplePlanner
from agent_forge.runtime.planning_mode import PlanningModePolicy
from agent_forge.runtime.run_lifecycle import RunLifecycle, StopRequest
from agent_forge.runtime.state import AgentRunSession
from agent_forge.runtime.task_state import TaskRunStatus, TaskStateStore, summarize_checkpoint
from agent_forge.runtime.tool_execution import ToolExecutionPipeline
from agent_forge.safety.guardrails import input_guardrail, output_guardrail
from agent_forge.skills import build_default_skill_registry
from agent_forge.skills.registry import SkillSpec
from agent_forge.tools.registry import ToolRegistry
from agent_forge.tools.tool_router import ToolRouter


@dataclass(frozen=True)
class PreparedTurn:
    """一次 LLM 调用所需的完整输入。"""

    step: int
    context_message: Message
    messages_for_llm: list[Message]
    schemas: list[ToolSchema]
    allowed_tool_names: set[str]
    history_chars: int
    tool_schema_chars: int


class AgentLoop:
    """单 Agent 控制循环。

    第一遍只读 ``run``：准备一次 run，重复执行 turn，最后统一停止。
    第二遍按需读 ``_prepare_run``、``_run_turn`` 和 ``_prepare_turn``。
    工具治理细节属于 ``ToolExecutionPipeline``，checkpoint/HITL/停止属于
    ``RunLifecycle``，不再挤在本类中。
    """

    def __init__(
        self,
        config: RuntimeConfig,
        trace: TraceRecorder,
        registry: ToolRegistry,
        llm: LLMClient | None = None,
    ) -> None:
        """接收 CLI 装配的依赖，不在运行中创建隐式全局对象。"""

        if llm is None:
            raise ValueError(
                "AgentLoop requires a real LLM client; "
                "build it through runtime.wiring.build_llm"
            )
        self.config = config
        self.trace = trace
        self.registry = registry
        self.llm = llm

        self.planner = SimplePlanner()
        self.clarification_policy = ClarificationPolicy()
        self.planning_mode_policy = PlanningModePolicy()
        self.tool_router = ToolRouter()
        self.skill_registry = build_default_skill_registry(
            getattr(config, "skill_manifest_files", [])
        )
        self.environment = getattr(
            config,
            "execution_environment",
            None,
        ) or ExecutionEnvironment(
            ExecutionEnvironmentConfig(workspace=config.workspace)
        )
        self.hooks = HookManager.default(
            self.environment,
            getattr(config, "auto_approve_writes", True),
            approval_mode=getattr(config, "approval_mode", "trusted"),
        )
        self.task_state_store = TaskStateStore(
            getattr(config, "task_state_root", ".agent_forge/task_state")
        )
        self.approval_store = ApprovalStore(
            getattr(config, "approval_root", ".agent_forge/approvals")
        )
        self.human_input_store = HumanInputStore(
            getattr(config, "human_input_root", ".agent_forge/human_input")
        )
        self.human_thread_id = (
            getattr(config, "human_thread_id", "") or self.trace.run_id
        )
        self.operation_ledger = OperationLedgerStore(
            getattr(
                config,
                "operation_ledger_root",
                ".agent_forge/operation_ledger",
            )
        )
        self.tool_execution = ToolExecutionPipeline(
            config,
            trace,
            registry,
            self.hooks,
            self.approval_store,
            self.operation_ledger,
        )

    # 第一遍：只读这个入口，掌握完整控制流。
    # PRIMARY ENTRYPOINT: execute the complete single-agent control loop.
    def run(self, task: str, agent_name: str = "CodingAgent") -> str:
        """运行四个阶段：start -> prepare -> turn loop -> stop。

        CLI、顺序多角色、fanout worker 和 benchmark 都调用这里。返回值只是最终
        文本；trace、checkpoint 与 operation ledger 保存可审计证据。
        """

        session = self._start_session(task, agent_name)
        stop = self._prepare_run(session)
        if stop is not None:
            return self._stop(session, stop)

        for step in range(1, session.max_iterations + 1):
            stop = self._run_turn(session, step)
            if stop is not None:
                return self._stop(session, stop)

        return self._stop(
            session,
            StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason="max_steps",
                final_answer="blocked: max_steps reached",
            ),
        )

    # 第二遍：按 start、prepare、turn 的顺序展开阶段实现。
    def _start_session(self, task: str, agent_name: str) -> AgentRunSession:
        """创建 checkpoint 和显式的 per-run state。"""

        self.trace.set_run_context(task=task)
        resume_summary = self._load_resume_summary(agent_name)
        checkpoint = self.task_state_store.start(
            run_id=self.trace.run_id,
            task=task,
            workspace=self.config.workspace,
            agent_name=agent_name,
            metadata={
                "execution_environment": self.environment.probe().to_dict(),
                "human_thread_id": self.human_thread_id,
            },
        )
        self.trace.record_task_state_checkpoint(
            step=0,
            agent_name=agent_name,
            checkpoint=checkpoint,
        )
        lifecycle = RunLifecycle(
            checkpoint=checkpoint,
            task_state_store=self.task_state_store,
            human_input_store=self.human_input_store,
            human_thread_id=self.human_thread_id,
            workspace=self.config.workspace,
            trace=self.trace,
            hooks=self.hooks,
        )
        return AgentRunSession(
            task=task,
            agent_name=agent_name,
            workspace_root=self.config.workspace,
            max_iterations=self.config.max_steps,
            lifecycle=lifecycle,
            controller=StepController.from_config(self.config),
            resume_summary=resume_summary,
        )

    def _prepare_run(self, session: AgentRunSession) -> StopRequest | None:
        """在第一次模型调用前完成 guardrail、澄清、planning 和 Skill 选择。"""

        input_check = input_guardrail(session.task)
        self.trace.add(
            0,
            session.agent_name,
            "guardrail_check",
            guardrail={
                "category": input_check.category,
                "passed": input_check.passed,
                "reason": input_check.reason,
                "severity": input_check.severity,
            },
        )
        if not input_check.passed:
            return StopRequest(
                TaskRunStatus.BLOCKED,
                "input_guardrail_block",
                f"blocked: {input_check.reason}",
            )

        clarification = self.clarification_policy.decide(session.task)
        self.trace.add(
            0,
            session.agent_name,
            "clarification_decision",
            clarification={
                "action": clarification.action,
                "confidence": clarification.confidence,
                "reason": clarification.reason,
                "question": clarification.question,
                "missing_fields": clarification.missing_fields,
            },
        )
        if clarification.action == "refuse":
            return StopRequest(
                TaskRunStatus.BLOCKED,
                "unsupported_task",
                f"blocked: {clarification.reason}",
            )
        if clarification.needs_user_input():
            resolution = session.lifecycle.request_human_input(
                agent_name=session.agent_name,
                kind="clarification",
                question=clarification.question,
                choices=[],
                reason=clarification.reason,
                step=0,
            )
            if resolution.stop is not None:
                return resolution.stop
            session.task = "\n".join(
                [
                    session.task,
                    "",
                    "Resolved operator clarification:",
                    f"Question: {resolution.request.question}",
                    f"Answer: {resolution.request.answer}",
                    "Continue from this answer and do not ask the same question again.",
                ]
            )
            self.trace.add(
                0,
                session.agent_name,
                "human_input_response_loaded",
                request=resolution.request.to_dict(),
            )

        planning_mode = self.planning_mode_policy.decide(session.task)
        self.trace.add(
            0,
            session.agent_name,
            "planning_mode",
            planning_mode={
                "mode": planning_mode.mode,
                "reason": planning_mode.reason,
                "complexity": planning_mode.complexity,
            },
        )

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
                }
                for skill in session.active_skills
            ],
            skill_mode=getattr(self.config, "skill_mode", "auto"),
        )

        session.messages = [Message("user", session.task)]
        session_summary = getattr(self.config, "session_summary", "")
        if session.resume_summary:
            session_summary = "\n".join(
                part
                for part in [session_summary, session.resume_summary]
                if part
            )
        session.memory.seed_session(
            previous_task=getattr(self.config, "previous_task", ""),
            session_summary=session_summary,
        )
        session.memory.set(
            "task",
            session.task,
            scope="session",
            source="user_task",
            agent_name=session.agent_name,
        )
        return None

    def _run_turn(
        self,
        session: AgentRunSession,
        step: int,
    ) -> StopRequest | None:
        """执行一个 turn：上下文 -> 模型 -> final answer 或工具管线。"""

        session.iteration = step
        turn = self._prepare_turn(session, step)
        response = self.llm.chat(turn.messages_for_llm, turn.schemas)

        if response.error:
            signal = session.controller.model_failure(response.error)
            self.trace.add(
                step,
                session.agent_name,
                "error",
                success=False,
                error=str(response.error),
            )
            self.trace.add(
                step,
                session.agent_name,
                "recovery_decision",
                success=signal.retryable,
                failure_kind=signal.kind.value,
                retryable=signal.retryable,
                recovery_hint=signal.recovery_hint,
            )
            return StopRequest(
                status=TaskRunStatus.FAILED,
                reason="invalid_llm_response",
                final_answer=f"blocked: invalid llm response: {response.error}",
                current_step=step,
                resume_hint=signal.recovery_hint,
            )

        self._record_llm_call(session, turn, response)
        usage = getattr(self.llm, "last_usage", None)
        session.estimated_cost_usd = float(
            getattr(usage, "estimated_cost_usd", 0.0) or 0.0
        )
        if not response.tool_calls:
            return self._finish_final_answer(session, response, step)
        return self.tool_execution.execute_calls(
            session,
            response,
            step=step,
            allowed_tool_names=turn.allowed_tool_names,
        )

    def _prepare_turn(
        self,
        session: AgentRunSession,
        step: int,
    ) -> PreparedTurn:
        """为当前 turn 路由工具并组装有预算的 context。"""

        session.lifecycle.update(
            status=TaskRunStatus.RUNNING,
            current_step=step,
            messages_count=len(session.messages),
            observations_count=len(session.observations),
            resume_hint=(
                "Rerun with --resume-state to seed this task state into a continuation."
            ),
        )

        repo_map = build_repo_map(self.config.workspace)
        route = self.tool_router.route(
            session.task,
            self.registry.schemas(),
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

        context_report = build_context_report(
            session.task,
            repo_map,
            session.memory,
            docs=repo_map.splitlines(),
            root=self.config.workspace,
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
                    f"{skill.name}@{skill.version}"
                    for skill in session.active_skills
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
        plan = self.planner.plan(session.task, step, context_report)
        self.trace.add(
            step,
            session.agent_name,
            "plan",
            plan={
                "goal": plan.goal,
                "reasoning_summary": plan.reasoning_summary,
                "next_action": plan.next_action,
            },
        )

        context_message = Message("system", context_report.render())
        return PreparedTurn(
            step=step,
            context_message=context_message,
            messages_for_llm=[context_message, *session.messages],
            schemas=schemas,
            allowed_tool_names=allowed_tool_names,
            history_chars=sum(
                len(message.content or "")
                + len(str(message.tool_calls or ""))
                + len(message.reasoning_content or "")
                for message in session.messages
            ),
            tool_schema_chars=sum(len(str(schema)) for schema in schemas),
        )

    # 第三遍：只有调试模型边界、final answer 或 resume 时才读以下方法。
    def _record_llm_call(
        self,
        session: AgentRunSession,
        turn: PreparedTurn,
        response: AgentResponse,
    ) -> None:
        """记录模型边界的输入规模、输出摘要和 provider usage。"""

        usage = getattr(self.llm, "last_usage", None)
        self.trace.add(
            turn.step,
            session.agent_name,
            "llm_call",
            llm_request_summary=(
                f"messages={len(turn.messages_for_llm)} "
                f"tools={len(turn.schemas)} "
                f"context_chars={len(turn.context_message.content)}"
            ),
            llm_response_summary=response.content or "tool_calls",
            llm_input_breakdown_chars={
                "system_context": len(turn.context_message.content),
                "conversation_history": turn.history_chars,
                "tool_schemas": turn.tool_schema_chars,
            },
            model_usage=usage.to_dict() if usage is not None else {},
        )

    def _finish_final_answer(
        self,
        session: AgentRunSession,
        response: AgentResponse,
        step: int,
    ) -> StopRequest:
        """校验并记录不再请求工具的模型回答。"""

        if self._contains_raw_tool_call_markup(response.content or ""):
            final_answer = "blocked: pending_tool_call_at_stop"
            self.trace.add(
                step,
                session.agent_name,
                "final_answer",
                success=False,
                observation=final_answer,
                pending_tool_call=True,
            )
            return StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason="pending_tool_call_at_stop",
                final_answer=final_answer,
                current_step=step,
                messages_count=len(session.messages),
                observations_count=len(session.observations),
                resume_hint=(
                    "Increase step budget or keep required tools routed until the pending call executes."
                ),
            )

        citations = session.evidence.final_citations()
        evidence_text = ""
        if citations:
            evidence_text = "\n证据:\n" + "\n".join(
                f"- {item}" for item in citations
            )
        final_answer = (
            (response.content or "")
            + evidence_text
            + "\n未验证点: 未进行真实线上压测。"
        )
        output_check = output_guardrail(
            final_answer,
            session.ran_tests,
            session.blocked,
        )
        self.trace.add(
            step,
            session.agent_name,
            "guardrail_check",
            guardrail={
                "category": output_check.category,
                "passed": output_check.passed,
                "reason": output_check.reason,
                "severity": output_check.severity,
            },
        )
        self.trace.add(
            step,
            session.agent_name,
            "final_answer",
            observation=final_answer,
            evidence_refs=citations,
        )
        return StopRequest(
            status=TaskRunStatus.COMPLETED,
            reason="final_answer",
            final_answer=final_answer,
            current_step=step,
            messages_count=len(session.messages),
            observations_count=len(session.observations),
        )

    def _stop(self, session: AgentRunSession, request: StopRequest) -> str:
        """更新内存状态，并把唯一的 terminal transition 交给 lifecycle。"""

        session.status = (
            "completed"
            if request.status == TaskRunStatus.COMPLETED
            else "failed"
            if request.status == TaskRunStatus.FAILED
            else "stopped"
        )
        session.stop_reason = request.reason
        session.final_answer = request.final_answer
        return session.lifecycle.stop(request)

    def _load_resume_summary(self, agent_name: str) -> str:
        """把显式 checkpoint 摘要放入 prompt memory 和 trace。"""

        resume_state = getattr(self.config, "resume_state", "")
        if not resume_state:
            return ""
        checkpoint = TaskStateStore.load_path(resume_state)
        summary = summarize_checkpoint(checkpoint)
        self.trace.add(
            0,
            agent_name,
            "resume_state_loaded",
            resume_state=resume_state,
            checkpoint=checkpoint.to_dict(),
            resume_summary=summary,
        )
        return summary

    def _select_active_skills(self, task: str) -> list[SkillSpec]:
        """选择会同时影响 context 和 tool routing 的 Skill。"""

        mode = getattr(self.config, "skill_mode", "auto")
        if mode == "none":
            return []
        explicit_names = list(getattr(self.config, "skill_names", []) or [])
        return self.skill_registry.select_for_task(
            task,
            names=explicit_names or None,
            limit=3,
        )

    def _contains_raw_tool_call_markup(self, content: str) -> bool:
        """识别 provider 以文本返回的未执行工具请求。"""

        lowered = content.lower()
        return "tool_calls" in lowered and "invoke name=" in lowered

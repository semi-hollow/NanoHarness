"""Single Agent 的主控制循环。

首次阅读只展开 ``AgentLoop.run``。运行前决策、turn 输入、工具治理和最终答案均由
同目录中具名应用服务负责，本类只保留真正的控制流。
"""

from __future__ import annotations

from agent_forge.runtime.application.dependencies import RuntimeDependencies
from agent_forge.runtime.application.final_answer import FinalAnswerBuilder
from agent_forge.runtime.application.run_lifecycle import StopRequest
from agent_forge.runtime.application.run_control import ApplyRunControl
from agent_forge.runtime.application.run_preparation import RunPreparation
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.application.tool_execution import ToolExecutionPipeline
from agent_forge.runtime.application.turn_preparation import PreparedTurn, TurnPreparation
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import AgentResponse
from agent_forge.runtime.domain.governance import HookDecisionType, ModelHookContext
from agent_forge.runtime.domain.task import TaskRunStatus


class AgentLoop:
    """单 Agent 控制流的应用服务。

    完整链路：``run`` -> ``_run_turn`` -> model -> final answer/tool pipeline。
    第一遍无需展开其余应用服务；需要定位策略时，再按类名进入对应文件。
    """

    def __init__(
        self,
        config: RuntimeConfig,
        dependencies: RuntimeDependencies,
    ) -> None:
        """接收 composition root 装配的端口，不创建基础设施对象。"""

        self.config = config
        self.trace = dependencies.events
        self.llm = dependencies.model
        self.hooks = dependencies.hooks
        self.model_capabilities = dependencies.model_capabilities
        self.run_control = ApplyRunControl(dependencies.control, dependencies.events)
        human_thread_id = getattr(config, "human_thread_id", "") or self.trace.run_id
        self.run_preparation = RunPreparation(
            config,
            dependencies,
            human_thread_id=human_thread_id,
        )
        self.turn_preparation = TurnPreparation(
            config,
            dependencies.events,
            dependencies.context,
            dependencies.tools,
            dependencies.environment,
            dependencies.model_capabilities,
        )
        self.tool_execution = ToolExecutionPipeline(
            config,
            dependencies.events,
            dependencies.tools,
            dependencies.hooks,
            dependencies.approvals,
            dependencies.operations,
            dependencies.control,
            dependencies.model_capabilities,
        )
        self.final_answer = FinalAnswerBuilder(dependencies.events)

    # 主要入口：依次执行 run 初始化、前置准备、turn loop 和统一停止。
    def run(self, task: str, agent_name: str = "CodingAgent") -> str:
        """编排 Single-Agent 黄金主链，不拥有任一阶段的领域规则。

        流程位置：Runtime 的有界阶段编排器。
        规范上游：``Harness.run`` 装配完成的 Runtime。
        下一 owner：``RunPreparation``、``TurnPreparation``、模型端口、
        ``ToolExecutionPipeline``、``RunLifecycle``。
        状态与证据：返回值只是最终文本；checkpoint、trace 与 operation ledger 才是
        可恢复、可审计的运行事实。
        系统不变量：所有退出分支必须汇合到 ``RunLifecycle.stop``。
        删除/内联影响：会隐藏阶段顺序并让多个入口各自处理停止语义。
        """

        session = self.run_preparation.start(task, agent_name)
        control = self.run_control.check(session, 0)
        if control.stop is not None:
            return self._stop(session, control.stop)
        stop = self.run_preparation.execute(session)
        if stop is not None:
            return self._stop(session, stop)

        for step in range(1, session.max_iterations + 1):
            control = self.run_control.check(session, step)
            if control.stop is not None:
                return self._stop(session, control.stop)
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

    def _run_turn(
        self,
        session: AgentRunSession,
        step: int,
    ) -> StopRequest | None:
        """执行一个 turn：准备输入 -> 调用模型 -> final 或工具分支。"""

        session.iteration = step
        self.trace.add(
            step,
            session.agent_name,
            "turn_started",
            turn={"max_iterations": session.max_iterations},
        )
        turn = self.turn_preparation.execute(session, step)
        response, hook_stop = self._call_model(session, turn)
        if hook_stop is not None:
            return hook_stop
        if response is None:  # pragma: no cover - protected by _call_model
            raise AssertionError("model invocation returned no response")

        control = self.run_control.check(session, step)
        if control.stop is not None:
            return control.stop
        if control.steered:
            self.trace.add(
                step,
                session.agent_name,
                "recovery_decision",
                recovery_hint="discard model response and re-plan from operator steer",
                retryable=True,
                failure_kind="operator_steer",
            )
            return None

        budget_stop = self._budget_stop_request(session, step)
        if budget_stop is not None:
            return budget_stop

        if response.error and _is_context_overflow(response.error):
            recovered_turn = self.turn_preparation.execute(
                session,
                step,
                force_compaction=True,
            )
            self.trace.add(
                step,
                session.agent_name,
                "context_overflow_recovery",
                success=(
                    recovered_turn.compacted
                    and recovered_turn.estimated_prompt_tokens
                    < turn.estimated_prompt_tokens
                ),
                context_overflow={
                    "initial_error": response.error,
                    "tokens_before": turn.estimated_prompt_tokens,
                    "tokens_after": recovered_turn.estimated_prompt_tokens,
                    "compacted": recovered_turn.compacted,
                },
            )
            if (
                recovered_turn.compacted
                and recovered_turn.estimated_prompt_tokens
                < turn.estimated_prompt_tokens
            ):
                turn = recovered_turn
                response, hook_stop = self._call_model(session, turn)
                if hook_stop is not None:
                    return hook_stop
                if response is None:  # pragma: no cover - protected above
                    raise AssertionError("model recovery returned no response")
                budget_stop = self._budget_stop_request(session, step)
                if budget_stop is not None:
                    return budget_stop

        if response.error:
            return self._handle_model_failure(session, response, step)

        if not response.tool_calls:
            return self.final_answer.execute(session, response, step)
        return self.tool_execution.execute_calls(
            session,
            response,
            step=step,
            allowed_tool_names=turn.allowed_tool_names,
        )

    def _call_model(
        self,
        session: AgentRunSession,
        turn: PreparedTurn,
    ) -> tuple[AgentResponse | None, StopRequest | None]:
        """执行 before/after model Hook，并保留唯一模型调用证据路径。"""

        context = ModelHookContext(
            run_id=self.trace.run_id,
            step=turn.step,
            agent_name=session.agent_name,
            task=session.task,
            messages_count=len(turn.messages_for_llm),
            tool_count=len(turn.schemas),
            estimated_prompt_tokens=turn.estimated_prompt_tokens,
            compacted=turn.compacted,
        )
        hook_result = self.hooks.before_model(context)
        self.trace.add(
            turn.step,
            session.agent_name,
            "hook_check",
            hook_stage="before_model",
            hook_result=hook_result.to_dict(),
        )
        if hook_result.decision in {HookDecisionType.DENY, HookDecisionType.ASK}:
            return None, StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason="model_hook_blocked",
                final_answer=f"blocked: {hook_result.reason}",
                current_step=turn.step,
                resume_hint="Adjust the lifecycle hook or task before resuming.",
            )
        self.trace.add(
            turn.step,
            session.agent_name,
            "model_started",
            model_request={
                "messages_count": len(turn.messages_for_llm),
                "tool_count": len(turn.schemas),
                "estimated_prompt_tokens": turn.estimated_prompt_tokens,
                "compacted": turn.compacted,
            },
        )
        response = self.hooks.after_model(
            context,
            self.llm.chat(turn.messages_for_llm, turn.schemas),
        )
        self._accumulate_model_cost(session)
        self._record_llm_call(session, turn, response)
        return response, None

    def _budget_stop_request(
        self,
        session: AgentRunSession,
        step: int,
    ) -> StopRequest | None:
        """每次模型调用后立即检查累计成本和 wall-clock 预算。"""

        signal = session.controller.should_stop(
            step,
            estimated_cost_usd=session.estimated_cost_usd,
            include_step_limit=False,
        )
        if signal is None:
            return None
        return StopRequest(
            status=TaskRunStatus.BLOCKED,
            reason=signal.reason.replace(" ", "_"),
            final_answer=f"blocked: {signal.reason}",
            current_step=step,
            resume_hint=signal.recovery_hint,
        )

    def _accumulate_model_cost(self, session: AgentRunSession) -> None:
        """将每次 gateway 调用成本累加到 run，而不是只保留最后一次。"""

        usage = getattr(self.llm, "last_usage", None)
        session.estimated_cost_usd += float(
            getattr(usage, "estimated_cost_usd", 0.0) or 0.0
        )

    def _handle_model_failure(
        self,
        session: AgentRunSession,
        response: AgentResponse,
        step: int,
    ) -> StopRequest:
        """把 provider 失败转换为显式、可恢复的停止请求。"""

        error = response.error or {"code": "unknown_error"}
        signal = session.controller.model_failure(error)
        self.trace.add(
            step,
            session.agent_name,
            "error",
            success=False,
            error=str(error),
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
            final_answer=f"blocked: invalid llm response: {error}",
            current_step=step,
            resume_hint=signal.recovery_hint,
        )

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
                f"context_chars={len(turn.context_message.content)} "
                f"prompt_tokens_estimate={turn.estimated_prompt_tokens} "
                f"compacted={turn.compacted}"
            ),
            llm_response_summary=(
                f"error:{response.error.get('code', 'unknown')}"
                if response.error
                else response.content
                or ("tool_calls" if response.tool_calls else "empty_response")
            ),
            llm_input_breakdown_chars={
                "system_context": len(turn.context_message.content),
                "conversation_history": turn.history_chars,
                "tool_schemas": turn.tool_schema_chars,
            },
            model_usage=usage.to_dict() if usage is not None else {},
            response_normalization=response.normalization or {},
        )

    @staticmethod
    def _stop(session: AgentRunSession, request: StopRequest) -> str:
        """更新内存状态，并把唯一 terminal transition 交给 lifecycle。"""

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


def _is_context_overflow(error: dict[str, object]) -> bool:
    """识别主流 OpenAI-compatible 网关返回的窗口溢出错误。"""

    text = " ".join(str(value) for value in error.values()).lower()
    markers = [
        "context_length_exceeded",
        "maximum context length",
        "context window",
        "too many tokens",
        "prompt is too long",
    ]
    return any(marker in text for marker in markers)

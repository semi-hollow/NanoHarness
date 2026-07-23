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

        run_session = self.run_preparation.start(task, agent_name)
        initial_operator_control = self.run_control.check(run_session, 0)
        if initial_operator_control.stop is not None:
            return self._stop(run_session, initial_operator_control.stop)
        preparation_stop = self.run_preparation.execute(run_session)
        if preparation_stop is not None:
            return self._stop(run_session, preparation_stop)

        for step in range(1, run_session.max_iterations + 1):
            # 模型边界 1：先把已排队 steer 写成 user message，再组装本 turn 上下文。
            operator_control = self.run_control.check(run_session, step)
            if operator_control.stop is not None:
                return self._stop(run_session, operator_control.stop)
            turn_stop = self._run_turn(run_session, step)
            if turn_stop is not None:
                return self._stop(run_session, turn_stop)

        return self._stop(
            run_session,
            StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason="max_steps",
                final_answer="blocked: max_steps reached",
            ),
        )

    # region 第二层内部步骤（首次阅读可折叠）
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
        prepared_turn = self.turn_preparation.execute(session, step)
        model_response, hook_stop_request = self._call_model(
            session,
            prepared_turn,
        )
        if hook_stop_request is not None:
            return hook_stop_request
        if model_response is None:  # pragma: no cover - protected by _call_model
            raise AssertionError("model invocation returned no response")

        # 模型边界 2：模型调用期间到达的 steer 使本次 response 过时；丢弃后重规划。
        operator_control = self.run_control.check(session, step)
        if operator_control.stop is not None:
            return operator_control.stop
        if operator_control.steered:
            self.trace.add(
                step,
                session.agent_name,
                "recovery_decision",
                recovery_hint="discard model response and re-plan from operator steer",
                retryable=True,
                failure_kind="operator_steer",
            )
            return None

        budget_stop_request = self._budget_stop_request(session, step)
        if budget_stop_request is not None:
            return budget_stop_request

        if model_response.error and _is_context_overflow(model_response.error):
            compacted_turn = self.turn_preparation.execute(
                session,
                step,
                force_compaction=True,
            )
            self.trace.add(
                step,
                session.agent_name,
                "context_overflow_recovery",
                success=(
                    compacted_turn.compacted
                    and compacted_turn.estimated_prompt_tokens
                    < prepared_turn.estimated_prompt_tokens
                ),
                context_overflow={
                    "initial_error": model_response.error,
                    "tokens_before": prepared_turn.estimated_prompt_tokens,
                    "tokens_after": compacted_turn.estimated_prompt_tokens,
                    "compacted": compacted_turn.compacted,
                },
            )
            if (
                compacted_turn.compacted
                and compacted_turn.estimated_prompt_tokens
                < prepared_turn.estimated_prompt_tokens
            ):
                prepared_turn = compacted_turn
                model_response, hook_stop_request = self._call_model(
                    session,
                    prepared_turn,
                )
                if hook_stop_request is not None:
                    return hook_stop_request
                if model_response is None:  # pragma: no cover - protected above
                    raise AssertionError("model recovery returned no response")
                budget_stop_request = self._budget_stop_request(session, step)
                if budget_stop_request is not None:
                    return budget_stop_request

        if model_response.error:
            return self._handle_model_failure(session, model_response, step)

        if not model_response.tool_calls:
            return self.final_answer.execute(session, model_response, step)
        return self.tool_execution.execute_calls(
            session,
            model_response,
            step=step,
            allowed_tool_names=prepared_turn.allowed_tool_names,
        )

    def _call_model(
        self,
        session: AgentRunSession,
        prepared_turn: PreparedTurn,
    ) -> tuple[AgentResponse | None, StopRequest | None]:
        """执行 before/after model Hook，并保留唯一模型调用证据路径。"""

        model_hook_context = ModelHookContext(
            run_id=self.trace.run_id,
            step=prepared_turn.step,
            agent_name=session.agent_name,
            task=session.task,
            messages_count=len(prepared_turn.messages_for_llm),
            tool_count=len(prepared_turn.schemas),
            estimated_prompt_tokens=prepared_turn.estimated_prompt_tokens,
            compacted=prepared_turn.compacted,
        )
        before_model_decision = self.hooks.before_model(model_hook_context)
        self.trace.add(
            prepared_turn.step,
            session.agent_name,
            "hook_check",
            hook_stage="before_model",
            hook_result=before_model_decision.to_dict(),
        )
        if before_model_decision.decision in {
            HookDecisionType.DENY,
            HookDecisionType.ASK,
        }:
            return None, StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason="model_hook_blocked",
                final_answer=f"blocked: {before_model_decision.reason}",
                current_step=prepared_turn.step,
                resume_hint="Adjust the lifecycle hook or task before resuming.",
            )
        self.trace.add(
            prepared_turn.step,
            session.agent_name,
            "model_started",
            model_request={
                "messages_count": len(prepared_turn.messages_for_llm),
                "tool_count": len(prepared_turn.schemas),
                "estimated_prompt_tokens": prepared_turn.estimated_prompt_tokens,
                "compacted": prepared_turn.compacted,
            },
        )
        model_response = self.hooks.after_model(
            model_hook_context,
            self.llm.chat(
                prepared_turn.messages_for_llm,
                prepared_turn.schemas,
            ),
        )
        self._accumulate_model_cost(session)
        self._record_llm_call(session, prepared_turn, model_response)
        return model_response, None

    def _budget_stop_request(
        self,
        session: AgentRunSession,
        step: int,
    ) -> StopRequest | None:
        """每次模型调用后立即检查累计成本和 wall-clock 预算。"""

        budget_stop_signal = session.controller.should_stop(
            step,
            estimated_cost_usd=session.estimated_cost_usd,
            include_step_limit=False,
        )
        if budget_stop_signal is None:
            return None
        return StopRequest(
            status=TaskRunStatus.BLOCKED,
            reason=budget_stop_signal.reason.replace(" ", "_"),
            final_answer=f"blocked: {budget_stop_signal.reason}",
            current_step=step,
            resume_hint=budget_stop_signal.recovery_hint,
        )

    def _accumulate_model_cost(self, session: AgentRunSession) -> None:
        """将每次 gateway 调用成本累加到 run，而不是只保留最后一次。"""

        latest_model_usage = getattr(self.llm, "last_usage", None)
        session.estimated_cost_usd += float(
            getattr(latest_model_usage, "estimated_cost_usd", 0.0) or 0.0
        )

    def _handle_model_failure(
        self,
        session: AgentRunSession,
        model_response: AgentResponse,
        step: int,
    ) -> StopRequest:
        """把 provider 失败转换为显式、可恢复的停止请求。"""

        model_error = model_response.error or {"code": "unknown_error"}
        failure_signal = session.controller.model_failure(model_error)
        self.trace.add(
            step,
            session.agent_name,
            "error",
            success=False,
            error=str(model_error),
        )
        self.trace.add(
            step,
            session.agent_name,
            "recovery_decision",
            success=failure_signal.retryable,
            failure_kind=failure_signal.kind.value,
            retryable=failure_signal.retryable,
            recovery_hint=failure_signal.recovery_hint,
        )
        return StopRequest(
            status=TaskRunStatus.FAILED,
            reason="invalid_llm_response",
            final_answer=f"blocked: invalid llm response: {model_error}",
            current_step=step,
            resume_hint=failure_signal.recovery_hint,
        )

    def _record_llm_call(
        self,
        session: AgentRunSession,
        prepared_turn: PreparedTurn,
        model_response: AgentResponse,
    ) -> None:
        """记录模型边界的输入规模、输出摘要和 provider usage。"""

        latest_model_usage = getattr(self.llm, "last_usage", None)
        self.trace.add(
            prepared_turn.step,
            session.agent_name,
            "llm_call",
            llm_request_summary=(
                f"messages={len(prepared_turn.messages_for_llm)} "
                f"tools={len(prepared_turn.schemas)} "
                f"context_chars={len(prepared_turn.context_message.content)} "
                f"prompt_tokens_estimate={prepared_turn.estimated_prompt_tokens} "
                f"compacted={prepared_turn.compacted}"
            ),
            llm_response_summary=(
                f"error:{model_response.error.get('code', 'unknown')}"
                if model_response.error
                else model_response.content
                or (
                    "tool_calls"
                    if model_response.tool_calls
                    else "empty_response"
                )
            ),
            llm_input_breakdown_chars={
                "system_context": len(prepared_turn.context_message.content),
                "conversation_history": prepared_turn.history_chars,
                "tool_schemas": prepared_turn.tool_schema_chars,
            },
            model_usage=(
                latest_model_usage.to_dict()
                if latest_model_usage is not None
                else {}
            ),
            response_normalization=model_response.normalization or {},
        )

    @staticmethod
    def _stop(session: AgentRunSession, stop_request: StopRequest) -> str:
        """更新内存状态，并把唯一 terminal transition 交给 lifecycle。"""

        session.status = (
            "completed"
            if stop_request.status == TaskRunStatus.COMPLETED
            else "failed"
            if stop_request.status == TaskRunStatus.FAILED
            else "stopped"
        )
        session.stop_reason = stop_request.reason
        session.final_answer = stop_request.final_answer
        return session.lifecycle.stop(stop_request)
    # endregion 第二层内部步骤结束


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

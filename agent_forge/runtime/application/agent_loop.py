"""Single Agent 的主控制循环。

``AgentLoop.run`` 是主入口。运行前决策、turn 输入、工具治理和最终答案均由
同目录中具名应用服务负责，本类只保留控制流。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agent_forge.runtime.application.dependencies import RuntimeDependencies
from agent_forge.runtime.application.final_answer import FinalAnswerBuilder
from agent_forge.runtime.application.run_lifecycle import StopRequest
from agent_forge.runtime.application.run_control import RunControlHandler
from agent_forge.runtime.application.run_preparation import RunPreparation
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.application.tool_execution import ToolExecutionPipeline
from agent_forge.runtime.application.turn_preparation import (
    PreparedTurn,
    TurnPreparation,
)
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import AgentResponse
from agent_forge.runtime.domain.governance import HookDecisionType, ModelHookContext
from agent_forge.runtime.domain.task import TaskRunStatus


class TurnOutcomeKind(str, Enum):
    """一个 turn 结束后，主循环唯一允许的三个动作。"""

    CONTINUE = "continue"
    REPLAN = "replan"
    STOP = "stop"


@dataclass(frozen=True)
class TurnOutcome:
    """显式区分正常下一轮、steer 重规划和停止。"""

    kind: TurnOutcomeKind
    stop_request: StopRequest | None = None


class AgentLoop:
    """单 Agent 控制流的应用服务。

    完整链路：``run`` -> ``_run_turn`` -> model -> final answer/tool pipeline。
    具体策略由具名应用服务负责，可按类名定位对应文件。
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
        self.run_control_handler = RunControlHandler(
            dependencies.control,
            dependencies.events,
        )
        human_thread_id = config.human_thread_id or self.trace.run_id
        self.run_preparation = RunPreparation(
            config,
            dependencies,
            human_thread_id=human_thread_id,
        )
        self.turn_preparation = TurnPreparation(
            config,
            dependencies.events,
            dependencies.turn_system_context_assembler,
            dependencies.tools,
            dependencies.environment,
            dependencies.model_capabilities,
        )
        self.tool_execution_pipeline = ToolExecutionPipeline(
            config,
            dependencies.events,
            dependencies.tools,
            dependencies.hooks,
            dependencies.approvals,
            dependencies.operations,
            dependencies.control,
            dependencies.model_capabilities,
        )
        self.final_answer_builder = FinalAnswerBuilder(dependencies.events)

    # 主要入口：依次执行 run 初始化、前置准备、turn loop 和统一停止。
    def run(self, task: str, agent_name: str = "CodingAgent") -> str:
        """编排 Single-Agent 黄金主链，不拥有任一阶段的领域规则。

        流程位置：Runtime 的有界阶段编排器。
        规范上游：``Harness.run`` 装配完成的 Runtime。
        下一 owner：``RunPreparation``、``TurnPreparation``、模型端口、
        ``ToolExecutionPipeline``、``RunLifecycle``。
        状态与证据：返回值只是最终文本；checkpoint、trace 与操作状态表才是
        可恢复、可审计的运行事实。
        系统不变量：所有退出分支必须汇合到
        ``RunLifecycle.finalize_run``。
        删除/内联影响：会隐藏阶段顺序并让多个入口各自处理停止语义。
        """

        # region 1. 创建会话并处理首次控制/澄清屏障
        # 创建 session 后先消费 pause/cancel/steer，再执行输入策略、Memory 与澄清准备；
        # 任一步产生 StopRequest 都直接汇合到唯一终态 owner，而不进入 Turn Loop。
        run_session = self.run_preparation.create_session(task, agent_name)
        initial_operator_control = self.run_control_handler.consume_pending_signals(
            run_session,
            0,
        )
        if initial_operator_control.stop is not None:
            return self._finalize_run(
                run_session,
                initial_operator_control.stop,
            )
        preparation_stop = self.run_preparation.prepare_run(run_session)
        if preparation_stop is not None:
            return self._finalize_run(run_session, preparation_stop)
        # endregion 1. 创建会话并处理首次控制/澄清屏障结束

        # region 2. 有界 Turn Loop：每轮只编排，不复制阶段规则
        for step in range(1, run_session.max_iterations + 1):
            # 模型边界 1：先把已排队 steer 写成 user message，再组装本 turn 上下文。
            operator_control = self.run_control_handler.consume_pending_signals(
                run_session,
                step,
            )
            if operator_control.stop is not None:
                return self._finalize_run(
                    run_session,
                    operator_control.stop,
                )
            turn_outcome = self._run_turn(run_session, step)
            if turn_outcome.kind == TurnOutcomeKind.STOP:
                if turn_outcome.stop_request is None:  # pragma: no cover - invariant
                    raise AssertionError("STOP turn outcome requires StopRequest")
                return self._finalize_run(
                    run_session,
                    turn_outcome.stop_request,
                )
            # CONTINUE 表示工具事务完成；REPLAN 表示 steer 使旧响应失效。
            # 两者都进入下一 turn，但语义不再由 ``None`` 隐式承载。
        # endregion 2. 有界 Turn Loop结束

        # region 3. 预算耗尽收口：所有退出仍汇合到 RunLifecycle
        return self._finalize_run(
            run_session,
            StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason="max_steps",
                stop_output="blocked: max_steps reached",
            ),
        )
        # endregion 3. 预算耗尽收口结束

    # region 第二层内部步骤
    def _run_turn(
        self,
        session: AgentRunSession,
        step: int,
    ) -> TurnOutcome:
        """执行一个 turn：准备输入 -> 调用模型 -> final 或工具分支。"""

        # region 1. 准备模型输入并执行一次受 Hook 治理的模型调用
        # TurnPreparation 冻结本轮 context、可见工具和预算；_call_model 负责模型前后 Hook，
        # 因而模型响应只有通过 Hook 后才允许进入后续控制与工具分支。
        session.iteration = step
        self._record_turn_started(session, step)
        prepared_turn = self.turn_preparation.prepare_turn(session, step)
        model_response, hook_stop_request = self._call_model(
            session,
            prepared_turn,
        )
        if hook_stop_request is not None:
            return TurnOutcome(TurnOutcomeKind.STOP, hook_stop_request)
        if model_response is None:  # pragma: no cover - protected by _call_model
            raise AssertionError("model invocation returned no response")
        # endregion 1. 准备模型输入并执行一次受 Hook 治理的模型调用结束

        # region 2. 模型返回边界：优先消费操作员控制与预算信号
        # 模型边界 2：模型调用期间到达的 steer 使本次 response 过时；丢弃后重规划。
        operator_control = self.run_control_handler.consume_pending_signals(
            session,
            step,
        )
        if operator_control.stop is not None:
            return TurnOutcome(TurnOutcomeKind.STOP, operator_control.stop)
        if operator_control.steered:
            self._record_steer_replan(session, step)
            return TurnOutcome(TurnOutcomeKind.REPLAN)

        budget_stop_request = self._budget_stop_request(session, step)
        if budget_stop_request is not None:
            return TurnOutcome(TurnOutcomeKind.STOP, budget_stop_request)
        # endregion 2. 模型返回边界结束

        # region 3. 上下文溢出恢复：仅在确实缩小窗口后重试一次
        # Provider 明确报告窗口溢出时，强制生成更小的 PreparedTurn；只有 token 估算
        # 确实下降才重试，防止用同一请求盲重放并重复计费。
        if model_response.error and _is_context_overflow(model_response.error):
            compacted_turn = self.turn_preparation.prepare_turn(
                session,
                step,
                force_compaction=True,
            )
            recovery_reduced_context = (
                compacted_turn.compacted
                and compacted_turn.estimated_prompt_tokens
                < prepared_turn.estimated_prompt_tokens
            )
            self._record_context_overflow_recovery(
                session=session,
                initial_turn=prepared_turn,
                compacted_turn=compacted_turn,
                model_error=model_response.error,
                recovery_reduced_context=recovery_reduced_context,
            )
            if recovery_reduced_context:
                # 重试仍走同一个模型 Hook 和预算检查，恢复路径不能绕过正常治理。
                prepared_turn = compacted_turn
                model_response, hook_stop_request = self._call_model(
                    session,
                    prepared_turn,
                )
                if hook_stop_request is not None:
                    return TurnOutcome(TurnOutcomeKind.STOP, hook_stop_request)
                if model_response is None:  # pragma: no cover - protected above
                    raise AssertionError("model recovery returned no response")
                budget_stop_request = self._budget_stop_request(session, step)
                if budget_stop_request is not None:
                    return TurnOutcome(TurnOutcomeKind.STOP, budget_stop_request)
        # endregion 3. 上下文溢出恢复结束

        # region 4. 响应分流：失败、最终回答或工具治理三选一
        if model_response.error:
            return TurnOutcome(
                TurnOutcomeKind.STOP,
                self._handle_model_failure(session, model_response, step),
            )

        # FINALIZE 阶段对 structured ToolCall 和文本 ToolCall 使用同一拒绝路径，
        # 防止 provider 编码差异改变停止原因或让最终轮意外执行工具。
        if prepared_turn.phase == "finalize" or not model_response.tool_calls:
            return TurnOutcome(
                TurnOutcomeKind.STOP,
                self.final_answer_builder.build_stop_request(
                    session,
                    model_response,
                    step,
                ),
            )
        tool_stop = self.tool_execution_pipeline.execute_calls(
            session,
            model_response,
            step=step,
            allowed_tool_names=prepared_turn.allowed_tool_names,
        )
        if tool_stop is not None:
            return TurnOutcome(TurnOutcomeKind.STOP, tool_stop)
        return TurnOutcome(TurnOutcomeKind.CONTINUE)
        # endregion 4. 响应分流结束

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
            messages_count=len(prepared_turn.llm_messages),
            tool_count=len(prepared_turn.tool_schemas),
            estimated_prompt_tokens=prepared_turn.estimated_prompt_tokens,
            compacted=prepared_turn.compacted,
        )
        before_model_decision = self.hooks.before_model(model_hook_context)
        self._record_before_model_hook(
            session=session,
            prepared_turn=prepared_turn,
            hook_result=before_model_decision.to_dict(),
        )
        if before_model_decision.decision in {
            HookDecisionType.DENY,
            HookDecisionType.ASK,
        }:
            return None, StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason="model_hook_blocked",
                stop_output=f"blocked: {before_model_decision.reason}",
                current_step=prepared_turn.step,
                resume_hint="Adjust the lifecycle hook or task before resuming.",
            )
        self._record_model_started(session, prepared_turn)
        model_response = self.hooks.after_model(
            model_hook_context,
            self.llm.chat(
                prepared_turn.llm_messages,
                prepared_turn.tool_schemas,
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
            stop_output=f"blocked: {budget_stop_signal.reason}",
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
        self._record_model_failure(
            session=session,
            step=step,
            model_error=model_error,
            failure_kind=failure_signal.kind.value,
            retryable=failure_signal.retryable,
            recovery_hint=failure_signal.recovery_hint,
        )
        return StopRequest(
            status=TaskRunStatus.FAILED,
            reason="invalid_llm_response",
            stop_output=f"blocked: invalid llm response: {model_error}",
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
                f"messages={len(prepared_turn.llm_messages)} "
                f"tools={len(prepared_turn.tool_schemas)} "
                f"context_chars={len(prepared_turn.turn_system_message.content)} "
                f"prompt_tokens_estimate={prepared_turn.estimated_prompt_tokens} "
                f"compacted={prepared_turn.compacted}"
            ),
            llm_response_summary=(
                f"error:{model_response.error.get('code', 'unknown')}"
                if model_response.error
                else model_response.content
                or ("tool_calls" if model_response.tool_calls else "empty_response")
            ),
            # 一个模型响应可以批量返回多个 ToolCall；实时操作台用这个计数明确
            # “一次模型决策”和“多次顺序工具执行”的边界。
            tool_call_count=len(model_response.tool_calls),
            llm_input_breakdown_chars={
                "system_context": len(prepared_turn.turn_system_message.content),
                "conversation_history": prepared_turn.history_chars,
                "tool_schemas": prepared_turn.tool_schema_chars,
            },
            model_usage=(
                latest_model_usage.to_dict() if latest_model_usage is not None else {}
            ),
            response_normalization=model_response.normalization or {},
        )

    # region 证据记录器
    def _record_turn_started(self, session: AgentRunSession, step: int) -> None:
        """记录 turn 边界；step 表示模型决策轮次，不是事件序号。"""

        self.trace.add(
            step,
            session.agent_name,
            "turn_started",
            turn={"max_iterations": session.max_iterations},
        )

    def _record_steer_replan(self, session: AgentRunSession, step: int) -> None:
        """记录旧模型响应因 operator steer 到达而被丢弃。"""

        self.trace.add(
            step,
            session.agent_name,
            "recovery_decision",
            recovery_hint="discard model response and re-plan from operator steer",
            retryable=True,
            failure_kind="operator_steer",
        )

    def _record_context_overflow_recovery(
        self,
        *,
        session: AgentRunSession,
        initial_turn: PreparedTurn,
        compacted_turn: PreparedTurn,
        model_error: dict[str, object] | None,
        recovery_reduced_context: bool,
    ) -> None:
        """记录压缩前后 token 规模，证明恢复动作是否真正缩小输入。"""

        self.trace.add(
            initial_turn.step,
            session.agent_name,
            "context_overflow_recovery",
            success=recovery_reduced_context,
            context_overflow={
                "initial_error": model_error or {},
                "tokens_before": initial_turn.estimated_prompt_tokens,
                "tokens_after": compacted_turn.estimated_prompt_tokens,
                "compacted": compacted_turn.compacted,
            },
        )

    def _record_before_model_hook(
        self,
        *,
        session: AgentRunSession,
        prepared_turn: PreparedTurn,
        hook_result: dict,
    ) -> None:
        """记录模型调用前所有 Hook 合并后的门禁决定。"""

        self.trace.add(
            prepared_turn.step,
            session.agent_name,
            "hook_check",
            hook_stage="before_model",
            hook_result=hook_result,
        )

    def _record_model_started(
        self,
        session: AgentRunSession,
        prepared_turn: PreparedTurn,
    ) -> None:
        """记录实际发送给模型前的输入规模，不保存敏感 Prompt 正文。"""

        self.trace.add(
            prepared_turn.step,
            session.agent_name,
            "model_started",
            model_request={
                "messages_count": len(prepared_turn.llm_messages),
                "tool_count": len(prepared_turn.tool_schemas),
                "estimated_prompt_tokens": prepared_turn.estimated_prompt_tokens,
                "compacted": prepared_turn.compacted,
            },
        )

    def _record_model_failure(
        self,
        *,
        session: AgentRunSession,
        step: int,
        model_error: dict[str, object],
        failure_kind: str,
        retryable: bool,
        recovery_hint: str,
    ) -> None:
        """把 provider 原始错误和 Runtime 恢复判断保留为两条独立事实。"""

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
            success=retryable,
            failure_kind=failure_kind,
            retryable=retryable,
            recovery_hint=recovery_hint,
        )

    # endregion 证据记录器结束

    @staticmethod
    def _finalize_run(
        session: AgentRunSession,
        stop_request: StopRequest,
    ) -> str:
        """更新内存状态，并把唯一 terminal transition 交给 lifecycle。"""

        if stop_request.status == TaskRunStatus.COMPLETED:
            session.status = "completed"
        elif stop_request.status == TaskRunStatus.FAILED:
            session.status = "failed"
        else:
            session.status = "stopped"
        session.stop_reason = stop_request.reason
        session.stop_output = stop_request.stop_output
        return session.lifecycle.finalize_run(stop_request)

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

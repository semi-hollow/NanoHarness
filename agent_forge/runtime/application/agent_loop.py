"""Single Agent 的主控制循环。

``AgentLoop.run`` 是主入口。运行前决策、model step 输入、工具治理和最终答案均由
同目录中具名应用服务负责，本类只保留控制流。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agent_forge.runtime.application.dependencies import RuntimeDependencies
from agent_forge.runtime.application.final_answer import FinalAnswerBuilder
from agent_forge.runtime.application.run_lifecycle import StopRequest
from agent_forge.runtime.application.run_control import (
    RunControlHandler,
    RunControlOutcome,
)
from agent_forge.runtime.application.run_preparation import RunPreparation
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.application.tool_execution import ToolExecutionPipeline
from agent_forge.runtime.application.model_step_preparation import (
    PreparedModelStep,
    ModelStepPreparation,
)
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import AgentResponse
from agent_forge.runtime.domain.governance import HookDecisionType, ModelHookContext
from agent_forge.runtime.domain.task import TaskRunStatus


class ModelStepOutcomeKind(str, Enum):
    """一个 model step 结束后，主循环唯一允许的三个动作。"""

    CONTINUE = "continue"
    REFRESH_INPUT = "refresh_input"
    STOP = "stop"


@dataclass(frozen=True)
class ModelStepOutcome:
    """显式区分正常下一轮、模型输入变化后的重规划和停止。"""

    kind: ModelStepOutcomeKind
    stop_request: StopRequest | None = None


class AgentLoop:
    """单 Agent 控制流的应用服务。

    完整链路：``run`` -> ``_run_model_step`` -> model -> final answer/tool pipeline。
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
        self.run_preparation = RunPreparation(config, dependencies)
        self.model_step_preparation = ModelStepPreparation(
            config,
            dependencies.events,
            dependencies.system_context_assembler,
            dependencies.tools,
            dependencies.environment,
            dependencies.model_capabilities,
            dependencies.long_term_memory_recall,
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
            dependencies.conversation_threads,
        )
        self.final_answer_builder = FinalAnswerBuilder(dependencies.events)

    # 主要入口：依次执行 Run 初始化、前置准备、Model Step loop 和统一停止。
    def run(self, agent_name: str = "CodingAgent") -> str:
        """编排 Single-Agent 黄金主链，不拥有任一阶段的领域规则。

        流程位置：Runtime 的有界阶段编排器。
        规范上游：``Harness.run`` 装配完成的 Runtime。
        下一 owner：``RunPreparation``、``ModelStepPreparation``、模型端口、
        ``ToolExecutionPipeline``、``RunLifecycle``。
        状态与证据：返回值只是最终文本；checkpoint、trace 与操作状态表才是
        可恢复、可审计的运行事实。
        系统不变量：所有退出分支必须汇合到
        ``RunLifecycle.finalize_run``。
        删除/内联影响：会隐藏阶段顺序并让多个入口各自处理停止语义。
        """

        # region 1. 创建会话并处理首次控制/澄清屏障
        # 创建 session 后先消费 pause/cancel，再执行输入策略、Memory 与澄清准备；
        # 任一步产生 StopRequest 都直接汇合到唯一终态 owner，而不进入 Model Step loop。
        run_session = self.run_preparation.create_session(agent_name)
        initial_operator_control = self.run_control_handler.consume_pending_signals(
            run_session,
            0,
            include_model_input_signals=False,
            boundary="before_run",
        )
        # 首次边界只消费 terminal；steer/coordination 要等首个模型输入边界。
        if initial_operator_control.stop is not None:
            return self._finalize_run(
                run_session,
                initial_operator_control.stop,
            )
        preparation_stop = self.run_preparation.prepare_run(run_session)
        # 澄清、恢复或前置策略产生 StopRequest 时，不创建任何 Model Step。
        if preparation_stop is not None:
            return self._finalize_run(run_session, preparation_stop)

        # accepted final 已先写入 Thread、但进程可能在 checkpoint 前崩溃；恢复时
        # 直接幂等完成同一 Turn，不能再次调用模型生成第二个答案。
        existing_final_answer = run_session.lifecycle.existing_accepted_final_answer()
        # Thread 已有 accepted final 时只补齐终态，不重跑模型。
        if existing_final_answer is not None:
            return self._finalize_run(
                run_session,
                StopRequest(
                    status=TaskRunStatus.COMPLETED,
                    reason="final_answer",
                    stop_output=existing_final_answer,
                    candidate_final_answer=existing_final_answer,
                    current_step=run_session.lifecycle.checkpoint.current_step,
                    messages_count=len(run_session.messages),
                    observations_count=len(run_session.observations),
                ),
            )

        # Resume 同一 Turn 时先续跑 canonical assistant batch；模型不得重新提案。
        pending_batch = self.tool_execution_pipeline.resume_pending_calls(run_session)
        # 原 batch 再次到达等待或终态边界时，交给唯一 Lifecycle 收口。
        if pending_batch.stop_request is not None:
            return self._finalize_run(run_session, pending_batch.stop_request)
        first_model_step = (
            run_session.lifecycle.checkpoint.current_step + 1
            if pending_batch.resumed
            else 1
        )
        # endregion 1. 创建会话并处理首次控制/澄清屏障结束

        # region 2. 有界 Model Step Loop：每轮只编排，不复制阶段规则
        for step in range(first_model_step, run_session.max_iterations + 1):
            # 模型边界 1：先注入已排队的 steer/coordination，再组装当前 Model Step 上下文。
            run_control_outcome = self.run_control_handler.consume_pending_signals(
                run_session,
                step,
                boundary="before_model",
            )
            # terminal 仍优先于任何新模型输入，命中后直接走统一终态 owner。
            if run_control_outcome.stop is not None:
                return self._finalize_run(
                    run_session,
                    run_control_outcome.stop,
                )
            model_step_outcome = self._run_model_step(run_session, step)
            # 只有 STOP 离开循环；CONTINUE/REFRESH_INPUT 都由下一次迭代重建输入。
            if model_step_outcome.kind == ModelStepOutcomeKind.STOP:
                # STOP 没有 StopRequest 属于内部契约损坏，不能猜测终态。
                if model_step_outcome.stop_request is None:  # pragma: no cover - invariant
                    raise AssertionError("STOP model step outcome requires StopRequest")
                return self._finalize_run(
                    run_session,
                    model_step_outcome.stop_request,
                )
            # CONTINUE 表示工具事务完成；REFRESH_INPUT 表示新输入使旧响应失效。
            # 两者都进入下一 Model Step，但语义不再由 ``None`` 隐式承载。
        # endregion 2. 有界 Model Step Loop结束

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
    def _run_model_step(
        self,
        session: AgentRunSession,
        step: int,
    ) -> ModelStepOutcome:
        """执行一个 model step：准备输入 -> 调用模型 -> final 或工具分支。

        伪代码：冻结当前输入 -> 调用模型 -> 消费调用期间到达的控制/协调
        -> 必要时丢弃 stale response -> 尝试一次窗口恢复 -> 分流 final/tool/stop。
        """

        # region 1. 准备模型输入并执行一次受 Hook 治理的模型调用
        # ModelStepPreparation 冻结本轮 context、可见工具和预算；_call_model 负责模型前后 Hook，
        # 因而模型响应只有通过 Hook 后才允许进入后续控制与工具分支。
        session.iteration = step
        self._record_model_step_started(session, step)
        prepared_model_step = self.model_step_preparation.prepare_model_step(session, step)
        model_response, hook_stop_request = self._call_model(
            session,
            prepared_model_step,
        )
        # 决策 Hook 拒绝模型调用时，直接携带它生成的停止事实离开本 Model Step。
        if hook_stop_request is not None:
            return ModelStepOutcome(ModelStepOutcomeKind.STOP, hook_stop_request)
        # _call_model 正常路径必须返回响应；空值只能代表内部契约被破坏。
        if model_response is None:  # pragma: no cover - protected by _call_model
            raise AssertionError("model invocation returned no response")
        # endregion 1. 准备模型输入并执行一次受 Hook 治理的模型调用结束

        # region 2. 模型返回边界：优先消费 Runtime 控制/协调与预算信号
        # 模型边界 2：调用期间到达的 steer/coordination 使 response 过时；丢弃后重规划。
        after_model_outcome = self._after_model_boundary(session, step)
        # helper 已把 terminal、stale response 或预算超限收敛为显式 ModelStepOutcome。
        if after_model_outcome is not None:
            return after_model_outcome
        # endregion 2. 模型返回边界结束

        # region 3. 上下文溢出恢复：仅在确实缩小窗口后重试一次
        # Provider 明确报告窗口溢出时，强制生成更小的 PreparedModelStep；只有 token 估算
        # 确实下降才重试，防止用同一请求盲重放并重复计费。
        if model_response.error and _is_context_overflow(model_response.error):
            compacted_model_step = self.model_step_preparation.prepare_model_step(
                session,
                step,
                force_compaction=True,
            )
            recovery_reduced_context = (
                compacted_model_step.compacted
                and compacted_model_step.estimated_prompt_tokens
                < prepared_model_step.estimated_prompt_tokens
            )
            self._record_context_overflow_recovery(
                session=session,
                initial_model_step=prepared_model_step,
                compacted_model_step=compacted_model_step,
                model_error=model_response.error,
                recovery_reduced_context=recovery_reduced_context,
            )
            # 只有确实压缩成功才允许一次额外模型调用，避免重复相同失败请求。
            if recovery_reduced_context:
                # 重试仍走同一个模型 Hook，返回后也必须重新经过
                # after_model 安全边界；否则请求期间到达的 cancel/steer/
                # coordination 会让旧 ToolCall 越过新输入而执行。
                prepared_model_step = compacted_model_step
                model_response, hook_stop_request = self._call_model(
                    session,
                    prepared_model_step,
                )
                # 恢复调用仍受相同 Hook 决策约束。
                if hook_stop_request is not None:
                    return ModelStepOutcome(ModelStepOutcomeKind.STOP, hook_stop_request)
                # 恢复路径也必须满足模型响应契约。
                if model_response is None:  # pragma: no cover - protected above
                    raise AssertionError("model recovery returned no response")
                recovery_boundary_outcome = self._after_model_boundary(session, step)
                # recovery 返回后与普通请求使用同一停止/过时判定。
                if recovery_boundary_outcome is not None:
                    return recovery_boundary_outcome
        # endregion 3. 上下文溢出恢复结束

        # region 4. 响应分流：失败、最终回答或工具治理三选一
        if model_response.error:
            return ModelStepOutcome(
                ModelStepOutcomeKind.STOP,
                self._handle_model_failure(session, model_response, step),
            )

        # FINALIZE 阶段对 structured ToolCall 和文本 ToolCall 使用同一拒绝路径，
        # 防止 provider 编码差异改变停止原因或让最终轮意外执行工具。
        if prepared_model_step.phase == "finalize" or not model_response.tool_calls:
            return ModelStepOutcome(
                ModelStepOutcomeKind.STOP,
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
            allowed_tool_names=prepared_model_step.allowed_tool_names,
        )
        # Tool pipeline 产生审批、人工输入或失败终态时，主循环统一收口。
        if tool_stop is not None:
            return ModelStepOutcome(ModelStepOutcomeKind.STOP, tool_stop)
        return ModelStepOutcome(ModelStepOutcomeKind.CONTINUE)
        # endregion 4. 响应分流结束

    def _call_model(
        self,
        session: AgentRunSession,
        prepared_model_step: PreparedModelStep,
    ) -> tuple[AgentResponse | None, StopRequest | None]:
        """执行 before/after model Hook，并保留唯一模型调用证据路径。"""

        model_hook_context = ModelHookContext(
            run_id=self.trace.run_id,
            step=prepared_model_step.step,
            agent_name=session.agent_name,
            task=session.root_task,
            messages_count=len(prepared_model_step.llm_messages),
            tool_count=len(prepared_model_step.tool_schemas),
            estimated_prompt_tokens=prepared_model_step.estimated_prompt_tokens,
            compacted=prepared_model_step.compacted,
        )
        before_model_decision = self.hooks.before_model(model_hook_context)
        self._record_before_model_hook(
            session=session,
            prepared_model_step=prepared_model_step,
            hook_result=before_model_decision.to_dict(),
        )
        # DENY/ASK 都不允许发起 provider 请求，直接返回显式 StopRequest。
        if before_model_decision.decision in {
            HookDecisionType.DENY,
            HookDecisionType.ASK,
        }:
            return None, StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason="model_hook_blocked",
                stop_output=f"blocked: {before_model_decision.reason}",
                current_step=prepared_model_step.step,
                resume_hint="Adjust the lifecycle hook or task before resuming.",
            )
        self._record_model_started(session, prepared_model_step)
        model_response = self.hooks.after_model(
            model_hook_context,
            self.llm.chat(
                prepared_model_step.llm_messages,
                prepared_model_step.tool_schemas,
            ),
        )
        self._accumulate_model_cost(session)
        self._record_llm_call(session, prepared_model_step, model_response)
        return model_response, None

    def _after_model_boundary(
        self,
        session: AgentRunSession,
        step: int,
    ) -> ModelStepOutcome | None:
        """每次 provider 返回后统一消费控制/协调与预算信号。"""

        run_control_outcome = self.run_control_handler.consume_pending_signals(
            session,
            step,
            boundary="after_model",
        )
        # pause/cancel 优先停止，不再使用已返回的模型结果。
        if run_control_outcome.stop is not None:
            return ModelStepOutcome(ModelStepOutcomeKind.STOP, run_control_outcome.stop)
        # steer/coordination 改变了模型输入；旧响应必须丢弃后重规划。
        if run_control_outcome.model_input_changed:
            self._record_stale_model_response(session, step, run_control_outcome)
            return ModelStepOutcome(ModelStepOutcomeKind.REFRESH_INPUT)

        # 成本和 wall-clock 已计入后再判断预算，超限响应不能执行 Tool。
        budget_stop_request = self._budget_stop_request(session, step)
        # 预算命中时丢弃当前响应，统一交给 Lifecycle 收口。
        if budget_stop_request is not None:
            return ModelStepOutcome(ModelStepOutcomeKind.STOP, budget_stop_request)
        return None

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
        # 没有预算停止信号时继续使用本次模型响应。
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
        prepared_model_step: PreparedModelStep,
        model_response: AgentResponse,
    ) -> None:
        """记录模型边界的输入规模、输出摘要和 provider usage。"""

        latest_model_usage = getattr(self.llm, "last_usage", None)
        self.trace.add(
            prepared_model_step.step,
            session.agent_name,
            "llm_call",
            llm_request_summary=(
                f"messages={len(prepared_model_step.llm_messages)} "
                f"tools={len(prepared_model_step.tool_schemas)} "
                f"context_chars={len(prepared_model_step.model_step_system_message.content)} "
                f"prompt_tokens_estimate={prepared_model_step.estimated_prompt_tokens} "
                f"compacted={prepared_model_step.compacted}"
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
                "system_context": len(prepared_model_step.model_step_system_message.content),
                "conversation_history": prepared_model_step.history_chars,
                "tool_schemas": prepared_model_step.tool_schema_chars,
            },
            model_usage=(
                latest_model_usage.to_dict() if latest_model_usage is not None else {}
            ),
            response_normalization=model_response.normalization or {},
        )

    # region 证据记录器
    def _record_model_step_started(self, session: AgentRunSession, step: int) -> None:
        """记录 model-step 边界；step 表示模型决策轮次，不是事件序号。"""

        self.trace.add(
            step,
            session.agent_name,
            "model_step_started",
            model_step={"max_iterations": session.max_iterations},
        )

    def _record_stale_model_response(
        self,
        session: AgentRunSession,
        step: int,
        outcome: RunControlOutcome,
    ) -> None:
        """模型返回后丢弃已被 steer 或 coordination 过时化的响应。"""

        self.trace.add(
            step,
            session.agent_name,
            "recovery_decision",
            recovery_hint="discard stale model response and rebuild current input",
            model_step_outcome=ModelStepOutcomeKind.REFRESH_INPUT.value,
            retryable=True,
            failure_kind=(
                "runtime_coordination"
                if outcome.coordinated
                else "operator_steer"
            ),
        )

    def _record_context_overflow_recovery(
        self,
        *,
        session: AgentRunSession,
        initial_model_step: PreparedModelStep,
        compacted_model_step: PreparedModelStep,
        model_error: dict[str, object] | None,
        recovery_reduced_context: bool,
    ) -> None:
        """记录压缩前后 token 规模，证明恢复动作是否真正缩小输入。"""

        self.trace.add(
            initial_model_step.step,
            session.agent_name,
            "context_overflow_recovery",
            success=recovery_reduced_context,
            context_overflow={
                "initial_error": model_error or {},
                "tokens_before": initial_model_step.estimated_prompt_tokens,
                "tokens_after": compacted_model_step.estimated_prompt_tokens,
                "compacted": compacted_model_step.compacted,
            },
        )

    def _record_before_model_hook(
        self,
        *,
        session: AgentRunSession,
        prepared_model_step: PreparedModelStep,
        hook_result: dict,
    ) -> None:
        """记录模型调用前所有 Hook 合并后的门禁决定。"""

        self.trace.add(
            prepared_model_step.step,
            session.agent_name,
            "hook_check",
            hook_stage="before_model",
            hook_result=hook_result,
        )

    def _record_model_started(
        self,
        session: AgentRunSession,
        prepared_model_step: PreparedModelStep,
    ) -> None:
        """记录实际发送给模型前的输入规模，不保存敏感 Prompt 正文。"""

        self.trace.add(
            prepared_model_step.step,
            session.agent_name,
            "model_started",
            model_request={
                "messages_count": len(prepared_model_step.llm_messages),
                "tool_count": len(prepared_model_step.tool_schemas),
                "estimated_prompt_tokens": prepared_model_step.estimated_prompt_tokens,
                "compacted": prepared_model_step.compacted,
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

        # 内存字符串只做轻量展示投影；TaskRunStatus 仍由 lifecycle 持久化。
        if stop_request.status == TaskRunStatus.COMPLETED:
            session.status = "completed"
        # provider/runtime 明确失败与普通暂停、阻断分开显示。
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

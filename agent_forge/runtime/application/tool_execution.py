"""模型工具请求的确定性治理管线。

两个核心方法构成执行主链：

1. ``execute_calls``：一次模型响应的公开入口，决定本轮真正处理哪些调用。
2. ``_execute_call``：单个调用的主干，按治理顺序把请求送到工具或暂停点。

其余私有方法都是这条主干的叶子规则，不会被外围直接调用。完整链路是：
``选择调用 -> 路由检查 -> HITL 屏障 -> 操作状态 -> 连续重复策略 -> 授权 -> 执行 -> 证据``。
执行授权、操作状态和反馈格式分别由 ``tool_authorization.py``、
``operation_tracker.py`` 和 ``tool_feedback.py`` 拥有。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agent_forge.contracts import JsonObject
from agent_forge.runtime.application.operation_tracker import (
    OperationIntent,
    OperationTracker,
)
from agent_forge.runtime.application.run_lifecycle import StopRequest
from agent_forge.runtime.application.run_control import RunControlHandler
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.application.tool_authorization import ToolAuthorizationGate
from agent_forge.runtime.application.tool_feedback import ToolFeedback
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.control import FailureSignal
from agent_forge.runtime.domain.conversation import (
    AgentResponse,
    Message,
    Observation,
    ToolCall,
)
from agent_forge.runtime.domain.human_input import HumanInputQuestion
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.domain.task import TaskCheckpointUpdate, TaskRunStatus
from agent_forge.runtime.ports import (
    ApprovalRepository,
    EventSink,
    HookPort,
    OperationLedgerRepository,
    RunControlPort,
    ToolGateway,
)
from agent_forge.safety.guardrails import GuardrailResult, tool_guardrail


class ToolCallStatus(str, Enum):
    """单个 ToolCall 离开治理管线时的明确结果。"""

    EXECUTED = "executed"
    SKIPPED = "skipped"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True, kw_only=True)
class ToolCallOutcome:
    """替代含义不清的 ``None``：说明工具是否执行，以及 run 是否停止。"""

    status: ToolCallStatus
    reason: str
    stop_request: StopRequest | None = None


class ToolExecutionPipeline:
    """把模型工具请求转换为受治理、可恢复的 Observation。

    本类只有 ``execute_calls`` 是外围入口。下划线方法是按执行阶段命名的内部步骤，
    每个步骤只拥有一种决策；它们不构成独立的公共 API。当前所有私有方法
    都由本类主链调用，没有预留但未接线的方法。

    折叠后按下面的纵向顺序读即可：

    ``execute_calls`` -> ``_select_calls_for_turn`` -> ``_execute_call``

    ``_execute_call`` 再按条件进入操作状态、重复策略、执行授权或真实执行分支；
    ``_run_tool`` 最后调用 evidence 叶子。
    """

    def __init__(
        self,
        config: RuntimeConfig,
        trace: EventSink,
        registry: ToolGateway,
        hooks: HookPort,
        approval_store: ApprovalRepository,
        operation_ledger: OperationLedgerRepository,
        run_control: RunControlPort,
        model_capabilities: ModelCapabilities,
    ) -> None:
        self.trace = trace
        self.tool_gateway = registry
        configured_tool_calls = max(1, int(config.max_tool_calls_per_turn))
        self.max_tool_calls_per_turn = (
            configured_tool_calls if model_capabilities.parallel_tool_calls else 1
        )
        self.run_control_handler = RunControlHandler(run_control, trace)
        self.tool_feedback = ToolFeedback(trace)
        self.operation_tracker = OperationTracker(
            config,
            trace,
            operation_ledger,
            self.tool_feedback,
        )
        self.authorization_gate = ToolAuthorizationGate(
            config,
            trace,
            hooks,
            approval_store,
            self.operation_tracker,
            self.tool_feedback,
        )

    # 主要入口：治理本 turn 的 ToolCall，在人工屏障或终止处返回 StopRequest。
    def execute_calls(
        self,
        session: AgentRunSession,
        response: AgentResponse,
        *,
        step: int,
        allowed_tool_names: set[str],
    ) -> StopRequest | None:
        """治理并执行一次模型响应中的 ToolCall，随后决定继续或停止。

        流程位置：模型意图进入真实工具、可能改变外部状态之前的治理管线。
        规范上游：``AgentLoop`` 的模型响应分支。
        下一 owner：``OperationTracker``、``ToolAuthorizationGate``、``ToolGateway``、
        ``RunLifecycle``。
        状态与证据：授权、operation、执行、Observation 与 citation 事件。
        系统不变量：状态变更操作先登记并通过确定性门；已执行操作只能回填既有结果。
        删除/内联影响：会失去统一状态变更治理与防重复执行边界。
        """

        # region 1. 批次整形：限制调用数，并把模型 ToolCall 写入会话协议
        # 先截断本轮调用并建立 assistant ToolCall 消息；后续每个已保留调用必须产生
        # tool Observation 或 StopRequest，保证 assistant/tool 协议不悬空。
        selected_tool_calls = self._select_calls_for_turn(session, response, step)
        session.messages.append(
            Message(
                role="assistant",
                content="",
                reasoning_content=response.reasoning_content,
                tool_calls=[
                    self.tool_feedback.to_message_tool_call(tool_call)
                    for tool_call in selected_tool_calls
                ],
            )
        )
        # endregion 1. 批次整形结束

        # region 2. 顺序执行：每个 ToolCall 都重新经过控制、安全与幂等边界
        # 同一模型响应可以包含多个 ToolCall，但这里故意顺序执行：前一个状态变更操作可能改变
        # 后一个调用的目标指纹，且操作员必须能在每项此类操作启动前 pause/cancel。
        for tool_call in selected_tool_calls:
            operator_control = self.run_control_handler.consume_pending_signals(
                session,
                step,
                include_steer=False,
            )
            if operator_control.stop is not None:
                return operator_control.stop
            tool_call_outcome = self._execute_call(
                session,
                tool_call,
                step=step,
                allowed_tool_names=allowed_tool_names,
            )
            if tool_call_outcome.stop_request is not None:
                return tool_call_outcome.stop_request
        # endregion 2. 顺序执行结束

        # region 3. Turn 续跑：无屏障时让 AgentLoop 开始下一轮
        return None
        # endregion 3. Turn 续跑结束

    # 核心主干：一个 ToolCall 从入口控制走到人工屏障、防重复、授权或执行。
    def _execute_call(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        *,
        step: int,
        allowed_tool_names: set[str],
    ) -> ToolCallOutcome:
        """让一个 ToolCall 依次经过路由、HITL、操作防重、重复保护和授权门。

        只有前述阶段均允许时才进入 ``_run_tool``；每个拒绝、等待、回填或执行分支都返回
        明确的 ``ToolCallOutcome``，并在对应阶段提交 Observation、Trace 或 Checkpoint。
        """

        # region 1. 调用意图预检：唯一计数器观察连续相同调用，并确认本轮可见性
        # 重复检测回答“模型是否原地打转”；路由复核回答“本轮是否向模型暴露该工具”。
        # 两者都在持久状态变化风险分类和授权前完成，但路由失败优先返回明确 Observation。
        repeat_limit_signal = (
            session.controller.observe_tool_intent_for_repeat_limit(tool_call)
        )
        tool_is_routed_for_this_turn = (
            self.tool_gateway.get(tool_call.name) is not None
            and tool_call.name in allowed_tool_names
        )
        guardrail_decision = tool_guardrail(
            tool_call.name,
            tool_call.arguments,
            exists=tool_is_routed_for_this_turn,
        )
        self._record_tool_guardrail(session, step, guardrail_decision)

        if not tool_is_routed_for_this_turn:
            self._handle_unrouted_tool(session, tool_call, step)
            return ToolCallOutcome(
                status=ToolCallStatus.FAILED,
                reason="tool_not_routed_for_this_turn",
            )
        # endregion 1. 调用意图预检结束

        # region 2. 协议分支：记录工具意图，ask_human 转入 durable HITL
        self._record_model_tool_intent(session, step, tool_call)
        if tool_call.name == "ask_human":
            return self._handle_human_question(session, tool_call, step)
        # endregion 2. 协议分支结束

        # tool_guardrail 只形成语义检查证据；真正的阻断条件是上面的路由复核结果。

        # region 3. 操作状态表：状态变更操作先复用确定结果，再考虑无进展重复
        # 这里是 OperationLedgerRepository（操作状态表）的唯一入口。ToolCall 只是模型给出的原始工具名和参数；
        # 在查询旧记录、申请权限或真正执行前，必须先得到三者共用的状态变更风险分类、
        # operation key 和执行前目标指纹。无需操作状态表治理的调用也会归一化，
        # 但不会创建 operation record。
        operation_intent = self.operation_tracker.build_operation_intent(tool_call)
        if operation_intent.side_effect:
            existing_operation = self.operation_tracker.resolve_existing_operation(
                session,
                tool_call,
                operation_intent,
                step,
            )
            if existing_operation.stop_request is not None:
                return ToolCallOutcome(
                    status=ToolCallStatus.STOPPED,
                    reason=existing_operation.stop_request.reason,
                    stop_request=existing_operation.stop_request,
                )
            if existing_operation.handled_without_execution:
                return ToolCallOutcome(
                    status=ToolCallStatus.SKIPPED,
                    reason="replayed_executed_operation_fact",
                )
        # endregion 3. 操作状态表结束

        # region 4. 连续重复策略：无持久状态变化的调用跳过，可能改变持久状态的调用停止
        if repeat_limit_signal is not None:
            return self._handle_exceeded_repeat_limit(
                session=session,
                tool_call=tool_call,
                operation_intent=operation_intent,
                repeat_limit_signal=repeat_limit_signal,
                step=step,
            )
        # endregion 4. 连续重复策略结束

        # region 5. 权限门与真实执行：只有 proceed 才能到达 ToolGateway
        # authorize 只返回治理结论，不执行工具；唯一真实执行入口仍是 _run_tool，
        # 从而保证 DENY、WAITING_APPROVAL 和 stale approval 都无法触达 ToolGateway。
        authorization_decision = self.authorization_gate.authorize(
            session,
            tool_call,
            operation_intent,
            step,
        )
        if authorization_decision.stop is not None:
            return ToolCallOutcome(
                status=ToolCallStatus.STOPPED,
                reason=authorization_decision.stop.reason,
                stop_request=authorization_decision.stop,
            )
        if not authorization_decision.proceed:
            return ToolCallOutcome(
                status=ToolCallStatus.FAILED,
                reason="tool_authorization_rejected",
            )
        return self._run_tool(session, tool_call, operation_intent, step)
        # endregion 5. 权限门与真实执行结束

    # region 分支与证据叶子
    # 批次整形：限制本 turn 调用数；ask_human 出现时建立同 turn 屏障。
    def _select_calls_for_turn(
        self,
        session: AgentRunSession,
        response: AgentResponse,
        step: int,
    ) -> list[ToolCall]:
        """按单轮上限截取普通 ToolCall；出现 ``ask_human`` 时只保留第一个问题。

        被预算截断或因 HITL 屏障延后的调用只写入证据，不在本 Turn 执行；continuation
        会让模型基于人工回答重新规划，而不是继续消费旧调用列表。
        """

        human_input_calls = [
            call for call in response.tool_calls if call.name == "ask_human"
        ]
        if not human_input_calls:
            selected_tool_calls = response.tool_calls[: self.max_tool_calls_per_turn]
            dropped_tool_calls = response.tool_calls[self.max_tool_calls_per_turn :]
            if dropped_tool_calls:
                self._record_tool_call_budget(
                    session=session,
                    step=step,
                    selected_tool_calls=selected_tool_calls,
                    dropped_tool_calls=dropped_tool_calls,
                )
            return selected_tool_calls

        selected_human_call = human_input_calls[0]
        deferred_tool_names = [
            call.name for call in response.tool_calls if call is not selected_human_call
        ]
        if deferred_tool_names:
            self._record_deferred_tool_calls(
                session=session,
                step=step,
                deferred_tool_names=deferred_tool_names,
            )
        return [selected_human_call]

    # 重复上限分支：操作状态表无可复用结果时，按是否可能改变持久状态决定“跳过”还是“停止”。
    def _handle_exceeded_repeat_limit(
        self,
        *,
        session: AgentRunSession,
        tool_call: ToolCall,
        operation_intent: OperationIntent,
        repeat_limit_signal: FailureSignal,
        step: int,
    ) -> ToolCallOutcome:
        """处理第三次连续相同调用；重复模型意图不等于工具已经执行。"""

        if not operation_intent.side_effect:
            return self._skip_repeated_call_without_durable_side_effect(
                session=session,
                tool_call=tool_call,
                repeat_limit_signal=repeat_limit_signal,
                step=step,
            )
        return self._block_repeated_side_effect_call(
            session=session,
            tool_call=tool_call,
            repeat_limit_signal=repeat_limit_signal,
            step=step,
        )

    def _skip_repeated_call_without_durable_side_effect(
        self,
        *,
        session: AgentRunSession,
        tool_call: ToolCall,
        repeat_limit_signal: FailureSignal,
        step: int,
    ) -> ToolCallOutcome:
        """不再执行第三次连续读取/观察，把换动作要求作为 Observation 回填。"""

        repeated_call_observation = Observation(
            tool_name=tool_call.name,
            success=False,
            content=(
                f"skipped consecutive identical call: {tool_call.name}; "
                "the first attempt and one retry already produced all available evidence; "
                "change the tool or arguments before trying again"
            ),
        )
        self.tool_feedback.append_tool_observation(
            session,
            tool_call,
            repeated_call_observation,
            step,
        )
        self._record_recovery_decision(
            session=session,
            step=step,
            failure_signal=repeat_limit_signal,
            run_can_continue=True,
            recovery_hint=(
                "Use the existing observation, inspect a different target, or make a "
                "state-changing edit before reading the same target again."
            ),
        )
        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(
                status=TaskRunStatus.RUNNING,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=repeated_call_observation.content,
                resume_hint=(
                    "The third consecutive identical observation call was skipped; "
                    "continue with a materially different action."
                ),
            )
        )
        return ToolCallOutcome(
            status=ToolCallStatus.SKIPPED,
            reason="consecutive_call_without_durable_side_effect_skipped",
        )

    def _block_repeated_side_effect_call(
        self,
        *,
        session: AgentRunSession,
        tool_call: ToolCall,
        repeat_limit_signal: FailureSignal,
        step: int,
    ) -> ToolCallOutcome:
        """状态变更操作连续请求三次且操作状态表无确定结果时，停止而不执行第三次。"""

        self._record_runtime_error(
            session=session,
            step=step,
            error=repeat_limit_signal.reason,
        )
        self._record_recovery_decision(
            session=session,
            step=step,
            failure_signal=repeat_limit_signal,
            run_can_continue=False,
            recovery_hint=repeat_limit_signal.recovery_hint,
        )
        repeated_side_effect_stop = StopRequest(
            status=TaskRunStatus.BLOCKED,
            reason="repeated_tool_call",
            final_answer="blocked: repeated tool call",
            current_step=step,
            last_tool=tool_call.name,
            resume_hint=repeat_limit_signal.recovery_hint,
        )
        return ToolCallOutcome(
            status=ToolCallStatus.STOPPED,
            reason="consecutive_side_effect_call_blocked",
            stop_request=repeated_side_effect_stop,
        )

    # 路由失败分支：生成失败 Observation，不调用不存在或本轮不可见的工具。
    def _handle_unrouted_tool(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        step: int,
    ) -> None:
        """异常分支：记录模型调用了本轮不可见工具，不触发真实工具。"""

        session.blocked = True
        unrouted_tool_observation = Observation(
            tool_name=tool_call.name,
            success=False,
            content=f"tool not routed for this turn: {tool_call.name}",
        )
        self.tool_feedback.append_tool_observation(
            session,
            tool_call,
            unrouted_tool_observation,
            step,
        )
        recovery_signal = self.tool_feedback.record_recovery_decision(
            session,
            unrouted_tool_observation,
            step,
        )
        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(
                status=TaskRunStatus.BLOCKED,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=unrouted_tool_observation.content[:600],
                resume_hint=(
                    recovery_signal.recovery_hint
                    if recovery_signal is not None
                    else "Tool was not available in this routed turn."
                ),
            )
        )

    # HITL 分支：读取已有回答，或持久化问题并返回 waiting_human。
    def _handle_human_question(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        step: int,
    ) -> ToolCallOutcome:
        """阶段 2：把 ask_human 转成持久化回答或 waiting_human 暂停。"""

        # region 1. 参数校验：坏问题作为 Observation 回填，不建立无效人工请求
        # ask_human 也是模型工具协议的一部分；参数错误应作为失败 Observation 反馈给模型，
        # 不能创建一个无法回答的持久化请求并让整个 Run 永久等待。
        question_arguments = tool_call.arguments or {}
        question_text = question_arguments.get("question")
        choice_values = question_arguments.get("choices", [])
        validation_error = ""
        if not isinstance(question_text, str) or not question_text.strip():
            validation_error = "invalid arguments: question must be non-empty str"
        elif not isinstance(choice_values, list) or any(
            not isinstance(choice, str) for choice in choice_values
        ):
            validation_error = "invalid arguments: choices must be list"

        if validation_error:
            invalid_question_observation = Observation(
                tool_name=tool_call.name,
                success=False,
                content=validation_error,
            )
            self.tool_feedback.append_tool_observation(
                session,
                tool_call,
                invalid_question_observation,
                step,
            )
            session.lifecycle.update_checkpoint(
                TaskCheckpointUpdate(
                    status=TaskRunStatus.RUNNING,
                    current_step=step,
                    last_tool=tool_call.name,
                    last_observation=invalid_question_observation.content,
                    resume_hint=(
                        "Retry ask_human with a non-empty question and a list of choices."
                    ),
                )
            )
            return ToolCallOutcome(
                status=ToolCallStatus.FAILED,
                reason="invalid_human_question",
            )
        # endregion 1. 参数校验结束

        # region 2. Durable barrier：已有回答直接返回；否则生成 waiting_human 停止请求
        # request_human_input 以稳定 request_id 查找同一问题：已有回答则继续，
        # 尚未回答则返回 StopRequest，由 AgentLoop 统一落成 WAITING_HUMAN checkpoint。
        human_input_resolution = session.lifecycle.request_human_input(
            HumanInputQuestion(
                agent_name=session.agent_name,
                kind="tool_question",
                question=str(question_text),
                choices=tuple(str(choice) for choice in choice_values),
                reason="model requested operator input",
                step=step,
            )
        )
        if human_input_resolution.stop is not None:
            return ToolCallOutcome(
                status=ToolCallStatus.STOPPED,
                reason=human_input_resolution.stop.reason,
                stop_request=human_input_resolution.stop,
            )
        # endregion 2. Durable barrier结束

        # region 3. 回答回填：人工输入变成普通 Tool Observation，协议继续
        human_answer_observation = Observation(
            tool_name=tool_call.name,
            success=True,
            content=f"human_response: {human_input_resolution.request.answer}",
        )
        self.tool_feedback.append_tool_observation(
            session,
            tool_call,
            human_answer_observation,
            step,
        )
        self._record_human_input_response_loaded(
            session=session,
            step=step,
            human_input_request_data=human_input_resolution.request.to_dict(),
        )
        return ToolCallOutcome(
            status=ToolCallStatus.EXECUTED,
            reason="human_response_loaded",
        )
        # endregion 3. 回答回填结束

    # 执行分支：最后一次控制检查后调用 ToolGateway，并提交状态与证据。
    def _run_tool(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        operation_intent: OperationIntent,
        step: int,
    ) -> ToolCallOutcome:
        """阶段 5：执行已获授权工具，再提交操作状态、证据和 checkpoint。"""

        # region 1. 最后控制边界：pause/cancel 阻止状态变更操作启动，steer 留到模型边界
        # 这是 ToolGateway 前最后一个 safe point。这里只消费终止类信号；steer 不能插在
        # assistant ToolCall 与 tool Observation 中间，必须留到下一模型边界。
        operator_control = self.run_control_handler.consume_pending_signals(
            session,
            step,
            include_steer=False,
        )
        if operator_control.stop is not None:
            return ToolCallOutcome(
                status=ToolCallStatus.STOPPED,
                reason=operator_control.stop.reason,
                stop_request=operator_control.stop,
            )
        # endregion 1. 最后控制边界结束

        # region 2. 幂等状态迁移：状态变更操作先进入 approved/executing，再调用工具
        # 手动审批路径此前已经创建操作状态；自动放行的状态变更操作也必须先落一条 approved
        # 记录，确保真实工具返回后 record_execution_result 一定有可迁移的持久化对象。
        if operation_intent.side_effect and not self.operation_tracker.has_record(
            operation_intent
        ):
            self.operation_tracker.ensure_planned(
                operation_intent,
                step=step,
                status="approved",
            )
        if operation_intent.side_effect:
            self.operation_tracker.record_executing(operation_intent, step=step)
        # endregion 2. 幂等状态迁移结束

        # region 3. 工具调用与证据提交：执行、after_tool 处理、操作状态、Observation
        # 执行顺序固定为：Gateway 返回原始 Observation -> after_tool 时机的具体处理器规范化/脱敏
        # -> 操作状态表提交执行结果 -> WorkingMemory/Evidence/Checkpoint 投影同一最终事实。
        self._record_tool_execution_started(session, step, tool_call)
        tool_observation = self.tool_gateway.execute(
            tool_call.name,
            tool_call.arguments,
        )
        tool_observation = self.authorization_gate.apply_after_tool_hooks(
            session,
            tool_call,
            operation_intent,
            tool_observation,
            step,
        )
        if operation_intent.side_effect:
            # 操作状态表记录工具执行后的最终 Observation，不能在 Gateway 调用前抢先写 executed。
            self.operation_tracker.record_execution_result(
                session,
                tool_call,
                operation_intent,
                tool_observation,
                step,
            )

        session.working_memory.add_observation(tool_observation)
        recorded_evidence = session.evidence.add_observation(tool_observation)
        validation_evidence = self.tool_feedback.build_validation_evidence(
            tool_call.name,
            tool_call.arguments or {},
            tool_observation,
        )
        if validation_evidence:
            session.ran_tests = (
                session.ran_tests or validation_evidence["status"] == "passed"
            )
            self._record_validation_evidence(
                session,
                step,
                validation_evidence,
            )
        self._record_execution_evidence(
            session,
            tool_call,
            tool_observation,
            recorded_evidence.citation() if recorded_evidence else "",
            step,
        )

        session.observations.append(tool_observation)
        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(
                status=TaskRunStatus.RUNNING,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=tool_observation.content[:600],
                messages_count=len(session.messages),
                observations_count=len(session.observations),
            )
        )
        self.tool_feedback.record_recovery_decision(
            session,
            tool_observation,
            step,
            remember=True,
        )
        # endregion 3. 工具调用与证据提交结束

        # region 4. Turn 收口：预算允许时补齐 tool message 供下一轮模型读取
        budget_stop_signal = session.controller.should_stop(
            step,
            estimated_cost_usd=session.estimated_cost_usd,
        )
        if budget_stop_signal is not None:
            budget_stop_request = StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason=budget_stop_signal.reason,
                final_answer=f"blocked: {budget_stop_signal.reason}",
                current_step=step,
                last_tool=tool_call.name,
                last_observation=tool_observation.content[:600],
                resume_hint=budget_stop_signal.recovery_hint,
            )
            return ToolCallOutcome(
                status=ToolCallStatus.STOPPED,
                reason=budget_stop_signal.reason,
                stop_request=budget_stop_request,
            )

        session.messages.append(
            Message(
                role="tool",
                content=tool_observation.content,
                name=tool_call.name,
                tool_call_id=tool_call.id,
            )
        )
        return ToolCallOutcome(
            status=(
                ToolCallStatus.EXECUTED
                if tool_observation.success
                else ToolCallStatus.FAILED
            ),
            reason=("tool_succeeded" if tool_observation.success else "tool_failed"),
        )
        # endregion 4. Turn 收口结束

    # region 证据记录器
    def _record_tool_call_budget(
        self,
        *,
        session: AgentRunSession,
        step: int,
        selected_tool_calls: list[ToolCall],
        dropped_tool_calls: list[ToolCall],
    ) -> None:
        """记录本轮工具预算截断；被丢弃的调用从未执行。"""

        self.trace.add(
            step,
            session.agent_name,
            "tool_calls_bounded",
            tool_call_budget={
                "limit": self.max_tool_calls_per_turn,
                "selected": [call.name for call in selected_tool_calls],
                "dropped": [call.name for call in dropped_tool_calls],
            },
        )

    def _record_deferred_tool_calls(
        self,
        *,
        session: AgentRunSession,
        step: int,
        deferred_tool_names: list[str],
    ) -> None:
        """记录 HITL barrier 延后的同轮工具调用。"""

        self.trace.add(
            step,
            session.agent_name,
            "tool_calls_deferred_for_human_input",
            deferred_tools=deferred_tool_names,
        )

    def _record_runtime_error(
        self,
        *,
        session: AgentRunSession,
        step: int,
        error: str,
    ) -> None:
        """记录阻断当前工具分支的 Runtime 错误。"""

        self.trace.add(
            step,
            session.agent_name,
            "error",
            success=False,
            error=error,
        )

    def _record_recovery_decision(
        self,
        *,
        session: AgentRunSession,
        step: int,
        failure_signal: FailureSignal,
        run_can_continue: bool,
        recovery_hint: str,
    ) -> None:
        """记录 Runtime 对当前失败是否允许继续的决定。"""

        self.trace.add(
            step,
            session.agent_name,
            "recovery_decision",
            success=run_can_continue,
            failure_kind=failure_signal.kind.value,
            retryable=failure_signal.retryable,
            recovery_hint=recovery_hint,
        )

    def _record_human_input_response_loaded(
        self,
        *,
        session: AgentRunSession,
        step: int,
        human_input_request_data: JsonObject,
    ) -> None:
        """记录本轮已消费哪一条持久化人工回答。"""

        self.trace.add(
            step,
            session.agent_name,
            "human_input_response_loaded",
            request=human_input_request_data,
        )

    def _record_tool_guardrail(
        self,
        session: AgentRunSession,
        step: int,
        decision: GuardrailResult,
    ) -> None:
        """记录模型工具意图的轻量语义检查结果。"""

        self.trace.add(
            step,
            session.agent_name,
            "guardrail_check",
            guardrail={
                "category": decision.category,
                "passed": decision.passed,
                "reason": decision.reason,
                "severity": decision.severity,
            },
        )

    def _record_model_tool_intent(
        self,
        session: AgentRunSession,
        step: int,
        tool_call: ToolCall,
    ) -> None:
        """记录模型提出的原始 ToolCall；它尚不代表工具已经执行。"""

        self.trace.add(
            step,
            session.agent_name,
            "action",
            tool_call=tool_call.name,
            tool_arguments=tool_call.arguments,
        )

    def _record_tool_execution_started(
        self,
        session: AgentRunSession,
        step: int,
        tool_call: ToolCall,
    ) -> None:
        """记录授权已通过、即将越过真实工具执行边界的事实。"""

        self.trace.add(
            step,
            session.agent_name,
            "tool_execution_started",
            tool_call=tool_call.name,
            tool_call_id=tool_call.id,
        )

    def _record_validation_evidence(
        self,
        session: AgentRunSession,
        step: int,
        validation_evidence: JsonObject,
    ) -> None:
        """记录测试通过、失败或环境不可用，供报告与 Failure Taxonomy 消费。"""

        self.trace.add(
            step,
            session.agent_name,
            "validation_evidence",
            success=validation_evidence["status"] == "passed",
            validation=validation_evidence,
        )

    # 证据叶子：把同一次执行投影为 call、observation、摘要和 citation 事件。
    def _record_execution_evidence(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        observation: Observation,
        citation: str,
        step: int,
    ) -> None:
        """证据叶子：把一次真实执行拆成 call、observation 与 citation。"""

        self.trace.add(
            step,
            session.agent_name,
            "tool_call",
            tool_call=tool_call.name,
            tool_call_id=tool_call.id,
            tool_arguments=tool_call.arguments,
        )
        self.trace.add(
            step,
            session.agent_name,
            "tool_observation",
            success=observation.success,
            execution_succeeded=observation.execution_succeeded,
            tool_call=tool_call.name,
            tool_call_id=tool_call.id,
            observation=observation.content,
        )
        self.trace.add(
            step,
            session.agent_name,
            "observation",
            success=observation.success,
            observation_summary=observation.content[:300],
        )
        if citation:
            self.trace.add(
                step,
                session.agent_name,
                "evidence_collected",
                evidence=citation,
            )

    # endregion 证据记录器结束

    # endregion 分支与证据叶子结束


__all__ = ["ToolExecutionPipeline"]

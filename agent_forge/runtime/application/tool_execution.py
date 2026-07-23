"""模型工具请求的确定性治理管线。

折叠阅读顺序只有两个方法：

1. ``execute_calls``：一次模型响应的公开入口，决定本轮真正处理哪些调用。
2. ``_execute_call``：单个调用的主干，按治理顺序把请求送到工具或暂停点。

其余私有方法都是这条主干的叶子规则，不会被外围直接调用。完整链路是：
``选择调用 -> 重复/路由检查 -> HITL 屏障 -> 幂等重放 -> 授权 -> 执行 -> 证据``。
审批、账本和反馈格式分别由 ``tool_authorization.py``、
``operation_tracker.py`` 和 ``tool_feedback.py`` 拥有。
"""

from __future__ import annotations

from agent_forge.runtime.application.operation_tracker import (
    OperationIntent,
    OperationTracker,
)
from agent_forge.runtime.application.run_lifecycle import StopRequest
from agent_forge.runtime.application.run_control import ApplyRunControl
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
from agent_forge.safety.guardrails import tool_guardrail


class ToolExecutionPipeline:
    """把模型工具请求转换为受治理、可恢复的 Observation。

    本类只有 ``execute_calls`` 是外围入口。下划线方法是按执行阶段命名的内部步骤，
    每个步骤只拥有一种决策；它们不是一组需要分别学习的公共 API。当前所有私有方法
    都由本类主链调用，没有预留但未接线的方法。

    折叠后按下面的纵向顺序读即可：

    ``execute_calls`` -> ``_select_calls_for_turn`` -> ``_execute_call``

    ``_execute_call`` 再按条件进入 repeat / unrouted / human / run-tool 分支；
    ``_run_tool`` 最后调用 evidence 叶子。静态规则叶子只判断重复调用是否可恢复。
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
        self.registry = registry
        configured_tool_calls = max(
            1, int(getattr(config, "max_tool_calls_per_turn", 4))
        )
        self.max_tool_calls_per_turn = (
            configured_tool_calls if model_capabilities.parallel_tool_calls else 1
        )
        self.run_control = ApplyRunControl(run_control, trace)
        self.feedback = ToolFeedback(trace)
        self.operations = OperationTracker(
            config,
            trace,
            operation_ledger,
            self.feedback,
        )
        self.authorization = ToolAuthorizationGate(
            config,
            trace,
            hooks,
            approval_store,
            self.operations,
            self.feedback,
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

        流程位置：模型意图进入真实工具与副作用之前的治理管线。
        规范上游：``AgentLoop`` 的模型响应分支。
        下一 owner：``OperationTracker``、``ToolAuthorizationGate``、``ToolGateway``、
        ``RunLifecycle``。
        状态与证据：授权、operation、执行、Observation 与 citation 事件。
        系统不变量：副作用先登记并通过确定性门；已执行操作只能重放证据。
        删除/内联影响：会失去统一副作用治理与幂等重放边界。
        """

        selected_tool_calls = self._select_calls_for_turn(session, response, step)
        session.messages.append(
            Message(
                "assistant",
                "",
                reasoning_content=response.reasoning_content,
                tool_calls=[
                    self.feedback.message_tool_call(tool_call)
                    for tool_call in selected_tool_calls
                ],
            )
        )
        for tool_call in selected_tool_calls:
            operator_control = self.run_control.check(
                session,
                step,
                include_steer=False,
            )
            if operator_control.stop is not None:
                return operator_control.stop
            tool_stop = self._execute_call(
                session,
                tool_call,
                step=step,
                allowed_tool_names=allowed_tool_names,
            )
            if tool_stop is not None:
                return tool_stop
        return None

    # 核心主干：一个 ToolCall 从 guardrail 走到人工屏障、幂等、授权或执行。
    def _execute_call(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        *,
        step: int,
        allowed_tool_names: set[str],
    ) -> StopRequest | None:
        """核心主干：检查 -> 控制信号 -> 幂等 -> 授权 -> 执行。"""

        # 准备区（首遍可略读）：建立稳定调用身份，并完成无副作用的策略判断。
        repeated_call_failure = session.controller.record_tool_intent(tool_call)
        tool_call_identity = (tool_call.name, str(tool_call.arguments))
        guardrail_decision = tool_guardrail(
            tool_call.name,
            tool_call.arguments,
            exists=(
                self.registry.get(tool_call.name) is not None
                and tool_call.name in allowed_tool_names
            ),
            repeated=(
                repeated_call_failure is not None
                or tool_call_identity in session.tool_history[-3:]
            ),
        )
        self.trace.add(
            step,
            session.agent_name,
            "guardrail_check",
            guardrail={
                "category": guardrail_decision.category,
                "passed": guardrail_decision.passed,
                "reason": guardrail_decision.reason,
                "severity": guardrail_decision.severity,
            },
        )

        if repeated_call_failure is not None:
            return self._handle_repeat(
                session,
                tool_call,
                repeated_call_failure,
                step,
            )
        if tool_call.name not in allowed_tool_names:
            self._record_unrouted_tool(session, tool_call, step)
            return None

        session.tool_history.append(tool_call_identity)
        self.trace.add(
            step,
            session.agent_name,
            "action",
            tool_call=tool_call.name,
            tool_arguments=tool_call.arguments,
        )
        if tool_call.name == "ask_human":
            return self._handle_human_question(session, tool_call, step)

        operation_intent = self.operations.describe(tool_call)
        if operation_intent.side_effect and self.operations.replay_if_executed(
            session,
            tool_call,
            operation_intent,
            step,
        ):
            return None

        authorization_decision = self.authorization.authorize(
            session,
            tool_call,
            operation_intent,
            step,
        )
        if authorization_decision.stop is not None:
            return authorization_decision.stop
        if not authorization_decision.proceed:
            return None
        return self._run_tool(session, tool_call, operation_intent, step)

    # region 分支与证据叶子（首次阅读可折叠）
    # 批次整形：限制本 turn 调用数；ask_human 出现时建立同 turn 屏障。
    def _select_calls_for_turn(
        self,
        session: AgentRunSession,
        response: AgentResponse,
        step: int,
    ) -> list[ToolCall]:
        """阶段 1：限制调用数量；HITL 出现时只保留第一个人工问题。"""

        human_input_calls = [
            call for call in response.tool_calls if call.name == "ask_human"
        ]
        if not human_input_calls:
            selected_tool_calls = response.tool_calls[
                : self.max_tool_calls_per_turn
            ]
            dropped_tool_calls = response.tool_calls[
                self.max_tool_calls_per_turn :
            ]
            if dropped_tool_calls:
                self.trace.add(
                    step,
                    session.agent_name,
                    "tool_calls_bounded",
                    tool_call_budget={
                        "limit": self.max_tool_calls_per_turn,
                        "selected": [
                            call.name for call in selected_tool_calls
                        ],
                        "dropped": [
                            call.name for call in dropped_tool_calls
                        ],
                    },
                )
            return selected_tool_calls

        selected_human_call = human_input_calls[0]
        deferred_tool_names = [
            call.name
            for call in response.tool_calls
            if call is not selected_human_call
        ]
        if deferred_tool_names:
            self.trace.add(
                step,
                session.agent_name,
                "tool_calls_deferred_for_human_input",
                deferred_tools=deferred_tool_names,
            )
        return [selected_human_call]

    # 重复分支：只读调用反馈后继续，重复副作用调用阻断本次 run。
    def _handle_repeat(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        failure_signal: FailureSignal,
        step: int,
    ) -> StopRequest | None:
        """异常分支：只读重复可恢复，副作用重复则阻断当前 run。"""

        if self._is_recoverable_repeated_tool(tool_call.name):
            repeated_call_observation = Observation(
                tool_call.name,
                False,
                (
                    f"repeated read-only tool call: {tool_call.name}; "
                    "use prior observation or choose a different tool"
                ),
            )
            self.feedback.append(
                session,
                tool_call,
                repeated_call_observation,
                step,
            )
            self.trace.add(
                step,
                session.agent_name,
                "recovery_decision",
                success=True,
                failure_kind=failure_signal.kind.value,
                retryable=True,
                recovery_hint=(
                    "Use existing read/search evidence, inspect a different symbol, "
                    "or proceed to apply_patch/git_diff."
                ),
            )
            session.lifecycle.update(
                TaskCheckpointUpdate(
                    status=TaskRunStatus.RUNNING,
                    current_step=step,
                    last_tool=tool_call.name,
                    last_observation=repeated_call_observation.content,
                    resume_hint=(
                        "Repeated read/search was skipped; continue with different evidence or edit."
                    ),
                )
            )
            return None

        self.trace.add(
            step,
            session.agent_name,
            "error",
            success=False,
            error=failure_signal.reason,
        )
        self.trace.add(
            step,
            session.agent_name,
            "recovery_decision",
            success=False,
            failure_kind=failure_signal.kind.value,
            retryable=failure_signal.retryable,
            recovery_hint=failure_signal.recovery_hint,
        )
        return StopRequest(
            status=TaskRunStatus.BLOCKED,
            reason="repeated_tool_call",
            final_answer="blocked: repeated tool call",
            current_step=step,
            last_tool=tool_call.name,
            resume_hint=failure_signal.recovery_hint,
        )

    # 路由失败分支：生成失败 Observation，不调用不存在或本轮不可见的工具。
    def _record_unrouted_tool(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        step: int,
    ) -> None:
        """异常分支：记录模型调用了本轮不可见工具，不触发真实工具。"""

        session.blocked = True
        unrouted_tool_observation = Observation(
            tool_call.name,
            False,
            f"tool not routed for this turn: {tool_call.name}",
        )
        self.feedback.append(
            session,
            tool_call,
            unrouted_tool_observation,
            step,
        )
        recovery_signal = self.feedback.record_recovery(
            session,
            unrouted_tool_observation,
            step,
        )
        session.lifecycle.update(
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
    ) -> StopRequest | None:
        """阶段 2：把 ask_human 转成持久化回答或 waiting_human 暂停。"""

        # 准备区：只解析并校验模型提供的人工问题参数。
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
                tool_call.name,
                False,
                validation_error,
            )
            self.feedback.append(
                session,
                tool_call,
                invalid_question_observation,
                step,
            )
            session.lifecycle.update(
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
            return None

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
            return human_input_resolution.stop

        human_answer_observation = Observation(
            tool_call.name,
            True,
            f"human_response: {human_input_resolution.request.answer}",
        )
        self.feedback.append(
            session,
            tool_call,
            human_answer_observation,
            step,
        )
        self.trace.add(
            step,
            session.agent_name,
            "human_input_response_loaded",
            request=human_input_resolution.request.to_dict(),
        )
        return None

    # 执行分支：最后一次控制检查后调用 ToolGateway，并提交状态与证据。
    def _run_tool(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        operation_intent: OperationIntent,
        step: int,
    ) -> StopRequest | None:
        """阶段 5：执行已获授权工具，再提交账本、证据和 checkpoint。"""

        # pause/cancel 必须阻止副作用；steer 不打断当前工具协议，留到下一模型边界。
        operator_control = self.run_control.check(
            session,
            step,
            include_steer=False,
        )
        if operator_control.stop is not None:
            return operator_control.stop
        if operation_intent.side_effect and not self.operations.exists(
            operation_intent
        ):
            self.operations.ensure_planned(
                operation_intent,
                step=step,
                status="approved",
            )
        self.trace.add(
            step,
            session.agent_name,
            "tool_execution_started",
            tool_call=tool_call.name,
            tool_call_id=tool_call.id,
        )
        tool_observation = self.registry.execute(
            tool_call.name,
            tool_call.arguments,
        )
        tool_observation = self.authorization.post_process(
            session,
            tool_call,
            operation_intent,
            tool_observation,
            step,
        )
        if operation_intent.side_effect:
            self.operations.record_result(
                session,
                tool_call,
                operation_intent,
                tool_observation,
                step,
            )

        session.working_memory.add_observation(tool_observation)
        recorded_evidence = session.evidence.add_observation(tool_observation)
        validation_evidence = self.feedback.validation_evidence(
            tool_call.name,
            tool_call.arguments or {},
            tool_observation,
        )
        if validation_evidence:
            session.ran_tests = (
                session.ran_tests
                or validation_evidence["status"] == "passed"
            )
            self.trace.add(
                step,
                session.agent_name,
                "validation_evidence",
                success=validation_evidence["status"] == "passed",
                validation=validation_evidence,
            )
        self._record_execution_evidence(
            session,
            tool_call,
            tool_observation,
            recorded_evidence.citation() if recorded_evidence else "",
            step,
        )

        session.observations.append(tool_observation)
        session.lifecycle.update(
            TaskCheckpointUpdate(
                status=TaskRunStatus.RUNNING,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=tool_observation.content[:600],
                messages_count=len(session.messages),
                observations_count=len(session.observations),
            )
        )
        self.feedback.record_recovery(
            session,
            tool_observation,
            step,
            remember=True,
        )

        budget_stop_signal = session.controller.should_stop(
            step,
            estimated_cost_usd=session.estimated_cost_usd,
        )
        if budget_stop_signal is not None:
            return StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason=budget_stop_signal.reason,
                final_answer=f"blocked: {budget_stop_signal.reason}",
                current_step=step,
                last_tool=tool_call.name,
                last_observation=tool_observation.content[:600],
                resume_hint=budget_stop_signal.recovery_hint,
            )

        session.messages.append(
            Message(
                "tool",
                tool_observation.content,
                name=tool_call.name,
                tool_call_id=tool_call.id,
            )
        )
        return None

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

    # 规则叶子：声明哪些重复调用没有副作用，可以由模型换方向后继续。
    @staticmethod
    def _is_recoverable_repeated_tool(tool_name: str) -> bool:
        """规则叶子：只有无副作用的重复读取可以反馈后继续。"""

        return tool_name in {
            "read_file",
            "grep",
            "grep_search",
            "list_files",
            "git_status",
            "git_diff",
            "diagnostics",
        }
    # endregion 分支与证据叶子结束


__all__ = ["ToolExecutionPipeline"]

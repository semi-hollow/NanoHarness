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
    每个步骤只拥有一种决策；它们不是一组需要分别学习的公共 API。
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
        """处理一次模型响应；返回值为空表示 AgentLoop 可以进入下一 turn。"""

        calls = self._select_calls_for_turn(session, response, step)
        session.messages.append(
            Message(
                "assistant",
                "",
                reasoning_content=response.reasoning_content,
                tool_calls=[self.feedback.message_tool_call(call) for call in calls],
            )
        )
        for tool_call in calls:
            control = self.run_control.check(session, step, include_steer=False)
            if control.stop is not None:
                return control.stop
            stop = self._execute_call(
                session,
                tool_call,
                step=step,
                allowed_tool_names=allowed_tool_names,
            )
            if stop is not None:
                return stop
        return None

    def _execute_call(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        *,
        step: int,
        allowed_tool_names: set[str],
    ) -> StopRequest | None:
        """核心主干：检查 -> 控制信号 -> 幂等 -> 授权 -> 执行。"""

        repeat_signal = session.controller.record_tool_intent(tool_call)
        key = (tool_call.name, str(tool_call.arguments))
        check = tool_guardrail(
            tool_call.name,
            tool_call.arguments,
            exists=(
                self.registry.get(tool_call.name) is not None
                and tool_call.name in allowed_tool_names
            ),
            repeated=repeat_signal is not None or key in session.tool_history[-3:],
        )
        self.trace.add(
            step,
            session.agent_name,
            "guardrail_check",
            guardrail={
                "category": check.category,
                "passed": check.passed,
                "reason": check.reason,
                "severity": check.severity,
            },
        )

        if repeat_signal is not None:
            return self._handle_repeat(session, tool_call, repeat_signal, step)
        if tool_call.name not in allowed_tool_names:
            self._record_unrouted_tool(session, tool_call, step)
            return None

        session.tool_history.append(key)
        self.trace.add(
            step,
            session.agent_name,
            "action",
            tool_call=tool_call.name,
            tool_arguments=tool_call.arguments,
        )
        if tool_call.name == "ask_human":
            return self._handle_human_question(session, tool_call, step)

        intent = self.operations.describe(tool_call)
        if intent.side_effect and self.operations.replay_if_executed(
            session,
            tool_call,
            intent,
            step,
        ):
            return None

        gate = self.authorization.authorize(session, tool_call, intent, step)
        if gate.stop is not None:
            return gate.stop
        if not gate.proceed:
            return None
        return self._run_tool(session, tool_call, intent, step)

    def _select_calls_for_turn(
        self,
        session: AgentRunSession,
        response: AgentResponse,
        step: int,
    ) -> list[ToolCall]:
        """阶段 1：限制调用数量；HITL 出现时只保留第一个人工问题。"""

        human_calls = [call for call in response.tool_calls if call.name == "ask_human"]
        if not human_calls:
            selected_calls = response.tool_calls[: self.max_tool_calls_per_turn]
            dropped = response.tool_calls[self.max_tool_calls_per_turn :]
            if dropped:
                self.trace.add(
                    step,
                    session.agent_name,
                    "tool_calls_bounded",
                    tool_call_budget={
                        "limit": self.max_tool_calls_per_turn,
                        "selected": [call.name for call in selected_calls],
                        "dropped": [call.name for call in dropped],
                    },
                )
            return selected_calls

        selected_human = human_calls[0]
        deferred = [
            call.name for call in response.tool_calls if call is not selected_human
        ]
        if deferred:
            self.trace.add(
                step,
                session.agent_name,
                "tool_calls_deferred_for_human_input",
                deferred_tools=deferred,
            )
        return [selected_human]

    def _handle_repeat(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        signal: FailureSignal,
        step: int,
    ) -> StopRequest | None:
        """异常分支：只读重复可恢复，副作用重复则阻断当前 run。"""

        if self._is_recoverable_repeated_tool(tool_call.name):
            observation = Observation(
                tool_call.name,
                False,
                (
                    f"repeated read-only tool call: {tool_call.name}; "
                    "use prior observation or choose a different tool"
                ),
            )
            self.feedback.append(session, tool_call, observation, step)
            self.trace.add(
                step,
                session.agent_name,
                "recovery_decision",
                success=True,
                failure_kind=signal.kind.value,
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
                    last_observation=observation.content,
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
            error=signal.reason,
        )
        self.trace.add(
            step,
            session.agent_name,
            "recovery_decision",
            success=False,
            failure_kind=signal.kind.value,
            retryable=signal.retryable,
            recovery_hint=signal.recovery_hint,
        )
        return StopRequest(
            status=TaskRunStatus.BLOCKED,
            reason="repeated_tool_call",
            final_answer="blocked: repeated tool call",
            current_step=step,
            last_tool=tool_call.name,
            resume_hint=signal.recovery_hint,
        )

    def _record_unrouted_tool(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        step: int,
    ) -> None:
        """异常分支：记录模型调用了本轮不可见工具，不触发真实工具。"""

        session.blocked = True
        observation = Observation(
            tool_call.name,
            False,
            f"tool not routed for this turn: {tool_call.name}",
        )
        self.feedback.append(session, tool_call, observation, step)
        signal = self.feedback.record_recovery(session, observation, step)
        session.lifecycle.update(
            TaskCheckpointUpdate(
                status=TaskRunStatus.BLOCKED,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=observation.content[:600],
                resume_hint=(
                    signal.recovery_hint
                    if signal is not None
                    else "Tool was not available in this routed turn."
                ),
            )
        )

    def _handle_human_question(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        step: int,
    ) -> StopRequest | None:
        """阶段 2：把 ask_human 转成持久化回答或 waiting_human 暂停。"""

        arguments = tool_call.arguments or {}
        question = arguments.get("question")
        choices = arguments.get("choices", [])
        validation_error = ""
        if not isinstance(question, str) or not question.strip():
            validation_error = "invalid arguments: question must be non-empty str"
        elif not isinstance(choices, list) or any(
            not isinstance(choice, str) for choice in choices
        ):
            validation_error = "invalid arguments: choices must be list"

        if validation_error:
            observation = Observation(tool_call.name, False, validation_error)
            self.feedback.append(session, tool_call, observation, step)
            session.lifecycle.update(
                TaskCheckpointUpdate(
                    status=TaskRunStatus.RUNNING,
                    current_step=step,
                    last_tool=tool_call.name,
                    last_observation=observation.content,
                    resume_hint=(
                        "Retry ask_human with a non-empty question and a list of choices."
                    ),
                )
            )
            return None

        resolution = session.lifecycle.request_human_input(
            HumanInputQuestion(
                agent_name=session.agent_name,
                kind="tool_question",
                question=str(question),
                choices=tuple(str(choice) for choice in choices),
                reason="model requested operator input",
                step=step,
            )
        )
        if resolution.stop is not None:
            return resolution.stop

        observation = Observation(
            tool_call.name,
            True,
            f"human_response: {resolution.request.answer}",
        )
        self.feedback.append(session, tool_call, observation, step)
        self.trace.add(
            step,
            session.agent_name,
            "human_input_response_loaded",
            request=resolution.request.to_dict(),
        )
        return None

    def _run_tool(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        step: int,
    ) -> StopRequest | None:
        """阶段 5：执行已获授权工具，再提交账本、证据和 checkpoint。"""

        control = self.run_control.check(session, step, include_steer=False)
        if control.stop is not None:
            return control.stop
        if intent.side_effect and not self.operations.exists(intent):
            self.operations.ensure_planned(
                intent,
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
        observation = self.registry.execute(tool_call.name, tool_call.arguments)
        observation = self.authorization.post_process(
            session,
            tool_call,
            intent,
            observation,
            step,
        )
        if intent.side_effect:
            self.operations.record_result(
                session,
                tool_call,
                intent,
                observation,
                step,
            )

        session.working_memory.add_observation(observation)
        evidence_item = session.evidence.add_observation(observation)
        validation = self.feedback.validation_evidence(
            tool_call.name,
            tool_call.arguments or {},
            observation,
        )
        if validation:
            session.ran_tests = session.ran_tests or validation["status"] == "passed"
            self.trace.add(
                step,
                session.agent_name,
                "validation_evidence",
                success=validation["status"] == "passed",
                validation=validation,
            )
        self._record_execution_evidence(
            session,
            tool_call,
            observation,
            evidence_item.citation() if evidence_item else "",
            step,
        )

        session.observations.append(observation)
        session.lifecycle.update(
            TaskCheckpointUpdate(
                status=TaskRunStatus.RUNNING,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=observation.content[:600],
                messages_count=len(session.messages),
                observations_count=len(session.observations),
            )
        )
        self.feedback.record_recovery(session, observation, step, remember=True)

        stop_signal = session.controller.should_stop(
            step,
            estimated_cost_usd=session.estimated_cost_usd,
        )
        if stop_signal is not None:
            return StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason=stop_signal.reason,
                final_answer=f"blocked: {stop_signal.reason}",
                current_step=step,
                last_tool=tool_call.name,
                last_observation=observation.content[:600],
                resume_hint=stop_signal.recovery_hint,
            )

        session.messages.append(
            Message(
                "tool",
                observation.content,
                name=tool_call.name,
                tool_call_id=tool_call.id,
            )
        )
        return None

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


__all__ = ["ToolExecutionPipeline"]

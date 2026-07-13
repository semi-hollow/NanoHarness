"""模型工具请求的确定性治理管线。

首次阅读只看 ``execute_calls`` 和 ``_execute_call``；其余私有方法按实际命中的
repeat、HITL、approval、ledger 或 execution 分支选择性展开。
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from typing import Any

from agent_forge.contracts import JsonObject, ToolArguments
from agent_forge.observability.trace import TraceRecorder
from agent_forge.runtime.approval import ApprovalStore
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.control import FailureSignal
from agent_forge.runtime.hooks import HookContext, HookDecisionType, HookManager
from agent_forge.runtime.llm_client import AgentResponse
from agent_forge.runtime.message import Message
from agent_forge.runtime.observation import Observation
from agent_forge.runtime.operation_ledger import OperationLedgerStore
from agent_forge.runtime.run_lifecycle import StopRequest
from agent_forge.runtime.state import AgentRunSession
from agent_forge.runtime.task_state import TaskRunStatus
from agent_forge.runtime.tool_call import ToolCall
from agent_forge.safety.guardrails import tool_guardrail
from agent_forge.tools.registry import ToolRegistry


@dataclass(frozen=True)
class OperationIntent:
    """一个工具调用在权限和幂等层面的身份。"""

    action: str
    command: str
    side_effect: bool
    key: str = ""
    fingerprint: dict[str, Any] | None = None


@dataclass(frozen=True)
class GateResult:
    """工具通过策略链后的下一步。"""

    proceed: bool
    stop: StopRequest | None = None


class ToolExecutionPipeline:
    """把模型的工具请求转换为受治理的 Observation。

    阅读顺序只有一条：``execute_calls`` -> ``_execute_call``。其余私有方法分别
    处理重复、HITL、幂等重放、审批和实际执行，调试对应分支时再展开。
    """

    def __init__(
        self,
        config: RuntimeConfig,
        trace: TraceRecorder,
        registry: ToolRegistry,
        hooks: HookManager,
        approval_store: ApprovalStore,
        operation_ledger: OperationLedgerStore,
    ) -> None:
        self.config = config
        self.trace = trace
        self.registry = registry
        self.hooks = hooks
        self.approval_store = approval_store
        self.operation_ledger = operation_ledger

    # 第一遍：只读 execute_calls 和 _execute_call，理解固定治理顺序。
    # RUNTIME PORT: govern and execute all tool calls from one model turn.
    def execute_calls(
        self,
        session: AgentRunSession,
        response: AgentResponse,
        *,
        step: int,
        allowed_tool_names: set[str],
    ) -> StopRequest | None:
        """按顺序处理一次模型响应中的工具调用，必要时返回停止请求。"""

        calls = self._select_calls_for_turn(session, response, step)
        session.messages.append(
            Message(
                "assistant",
                "",
                reasoning_content=response.reasoning_content,
                tool_calls=[self._message_tool_call(call) for call in calls],
            )
        )
        for tool_call in calls:
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
        """执行单个工具调用的固定治理链。"""

        repeat_signal = session.controller.record_tool_intent(tool_call)
        key = (tool_call.name, str(tool_call.arguments))
        tool_check = tool_guardrail(
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
                "category": tool_check.category,
                "passed": tool_check.passed,
                "reason": tool_check.reason,
                "severity": tool_check.severity,
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

        intent = self._build_operation_intent(tool_call)
        if intent.side_effect and self._handle_executed_operation(
            session,
            tool_call,
            intent,
            step,
        ):
            return None

        gate = self._authorize(session, tool_call, intent, step)
        if gate.stop is not None:
            return gate.stop
        if not gate.proceed:
            return None

        return self._run_tool(session, tool_call, intent, step)

    # 第二遍：任务命中哪个分支，就只展开对应方法。
    def _select_calls_for_turn(
        self,
        session: AgentRunSession,
        response: AgentResponse,
        step: int,
    ) -> list[ToolCall]:
        """让人工问题成为 barrier，推迟同一响应里的其他副作用。"""

        human_calls = [call for call in response.tool_calls if call.name == "ask_human"]
        if not human_calls:
            return response.tool_calls

        selected = human_calls[0]
        deferred = [call.name for call in response.tool_calls if call is not selected]
        if deferred:
            self.trace.add(
                step,
                session.agent_name,
                "tool_calls_deferred_for_human_input",
                deferred_tools=deferred,
            )
        return [selected]

    def _handle_repeat(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        signal: FailureSignal,
        step: int,
    ) -> StopRequest | None:
        """只读重复转为反馈；副作用重复直接停止。"""

        if self._is_recoverable_repeated_tool(tool_call.name):
            observation = Observation(
                tool_call.name,
                False,
                (
                    f"repeated read-only tool call: {tool_call.name}; "
                    "use prior observation or choose a different tool"
                ),
            )
            self._append_feedback(session, tool_call, observation, step)
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
                status=TaskRunStatus.RUNNING,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=observation.content,
                resume_hint=(
                    "Repeated read/search was skipped; continue with different evidence or edit."
                ),
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
        """把未路由工具作为可解释反馈返回模型。"""

        session.blocked = True
        observation = Observation(
            tool_call.name,
            False,
            f"tool not routed for this turn: {tool_call.name}",
        )
        self._append_feedback(session, tool_call, observation, step)
        signal = self._record_recovery(session, observation, step)
        session.lifecycle.update(
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

    def _handle_human_question(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        step: int,
    ) -> StopRequest | None:
        """校验模型问题，并转成持久化 HITL 状态。"""

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
            self._append_feedback(session, tool_call, observation, step)
            session.lifecycle.update(
                status=TaskRunStatus.RUNNING,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=observation.content,
                resume_hint=(
                    "Retry ask_human with a non-empty question and a list of choices."
                ),
            )
            return None

        resolution = session.lifecycle.request_human_input(
            agent_name=session.agent_name,
            kind="tool_question",
            question=str(question),
            choices=[str(choice) for choice in choices],
            reason="model requested operator input",
            step=step,
        )
        if resolution.stop is not None:
            return resolution.stop

        observation = Observation(
            tool_call.name,
            True,
            f"human_response: {resolution.request.answer}",
        )
        session.memory.add_observation(observation)
        session.messages.append(
            Message(
                "tool",
                observation.content,
                name=tool_call.name,
                tool_call_id=tool_call.id,
            )
        )
        self.trace.add(
            step,
            session.agent_name,
            "human_input_response_loaded",
            request=resolution.request.to_dict(),
        )
        return None

    def _build_operation_intent(self, tool_call: ToolCall) -> OperationIntent:
        """为副作用生成稳定 key 和目标指纹。"""

        action = self._permission_action(tool_call.name)
        command = str((tool_call.arguments or {}).get("command", ""))
        side_effect = self._is_side_effect_action(action)
        if not side_effect:
            return OperationIntent(action, command, False)
        return OperationIntent(
            action=action,
            command=command,
            side_effect=True,
            key=OperationLedgerStore.operation_key(
                tool_call.name,
                tool_call.arguments or {},
                self.config.workspace,
                action,
            ),
            fingerprint=OperationLedgerStore.operation_fingerprint(
                tool_call.name,
                tool_call.arguments or {},
                self.config.workspace,
                action,
            ),
        )

    def _handle_executed_operation(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        step: int,
    ) -> bool:
        """跳过可证明已执行的操作；目标变化时拒绝复用。"""

        existing = self.operation_ledger.get(intent.key)
        if existing is None or existing.status != "executed":
            return False

        stale = (
            existing.post_fingerprint is not None
            and not self._same_operation_fingerprint(
                intent.fingerprint,
                existing.post_fingerprint,
            )
        )
        if stale:
            observation = Observation(
                tool_call.name,
                False,
                (
                    "stale_operation_record: operation was executed before, "
                    f"but target state changed since then: {intent.key}"
                ),
            )
            self.trace.add(
                step,
                session.agent_name,
                "operation_ledger",
                operation_key=intent.key,
                operation_status="stale_operation_record",
                operation=existing.to_dict(),
                current_fingerprint=intent.fingerprint,
            )
            self._append_feedback(session, tool_call, observation, step)
            session.lifecycle.update(
                status=TaskRunStatus.BLOCKED,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=observation.content,
                messages_count=len(session.messages),
                observations_count=len(session.observations),
                resume_hint="Reread the target before reissuing a side-effect operation.",
            )
            return True

        observation = Observation(
            tool_call.name,
            True,
            f"skipped: operation already executed: {intent.key}",
        )
        self.trace.add(
            step,
            session.agent_name,
            "operation_ledger",
            operation_key=intent.key,
            operation_status="skipped_already_executed",
            operation=existing.to_dict(),
        )
        self._append_feedback(session, tool_call, observation, step)
        session.lifecycle.update(
            status=TaskRunStatus.RUNNING,
            current_step=step,
            last_tool=tool_call.name,
            last_observation=observation.content,
            messages_count=len(session.messages),
            observations_count=len(session.observations),
        )
        return True

    def _authorize(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        step: int,
    ) -> GateResult:
        """把 hook 的 ALLOW/DENY/ASK 决策分发给明确分支。"""

        hook_context = HookContext(
            run_id=self.trace.run_id,
            step=step,
            agent_name=session.agent_name,
            tool_name=tool_call.name,
            arguments=tool_call.arguments or {},
            action=intent.action,
            command=intent.command,
            auto_approve_writes=self.config.auto_approve_writes,
            approval_mode=getattr(self.config, "approval_mode", "trusted"),
        )
        hook_result = self.hooks.pre_tool(hook_context)
        self.trace.add(
            step,
            session.agent_name,
            "hook_check",
            hook_result=hook_result.to_dict(),
            tool_call=tool_call.name,
        )
        self.trace.add(
            step,
            session.agent_name,
            "permission_check",
            permission_decision=hook_result.decision.value,
            tool_call=tool_call.name,
            reason=hook_result.reason,
        )

        if hook_result.decision == HookDecisionType.DENY:
            return self._record_policy_denial(
                session,
                tool_call,
                reason=hook_result.reason,
                step=step,
            )
        if hook_result.decision == HookDecisionType.ASK:
            return self._resolve_approval(
                session,
                tool_call,
                intent,
                reason=hook_result.reason,
                step=step,
            )
        return GateResult(True)

    def _record_policy_denial(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        *,
        reason: str,
        step: int,
    ) -> GateResult:
        """把策略拒绝变成模型可见 Observation，而不是异常。"""

        session.blocked = True
        observation = Observation(
            tool_call.name,
            False,
            f"blocked: {reason}",
        )
        self._append_feedback(session, tool_call, observation, step)
        signal = self._record_recovery(session, observation, step)
        session.lifecycle.update(
            status=TaskRunStatus.BLOCKED,
            current_step=step,
            last_tool=tool_call.name,
            last_observation=observation.content,
            resume_hint=(
                signal.recovery_hint
                if signal is not None
                else "Action was blocked by runtime policy."
            ),
        )
        return GateResult(False)

    def _resolve_approval(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        *,
        reason: str,
        step: int,
    ) -> GateResult:
        """创建或读取审批，并拒绝复用目标已变化的批准。"""

        if intent.side_effect:
            self.operation_ledger.ensure_planned(
                intent.key,
                tool_call.name,
                tool_call.arguments or {},
                intent.action,
                self.config.workspace,
                run_id=self.trace.run_id,
                step=step,
                pre_fingerprint=intent.fingerprint,
            )
        approval = self.approval_store.get(intent.key)
        if approval is None and not self.config.auto_approve_writes:
            approval = self.approval_store.request(
                tool_name=tool_call.name,
                arguments=tool_call.arguments or {},
                action=intent.action,
                command=intent.command,
                workspace=self.config.workspace,
                run_id=self.trace.run_id,
                step=step,
                agent_name=session.agent_name,
                reason=reason,
                operation_fingerprint=intent.fingerprint,
            )
            if intent.side_effect:
                self.operation_ledger.record_pending(
                    intent.key,
                    tool_call.name,
                    tool_call.arguments or {},
                    intent.action,
                    self.config.workspace,
                    run_id=self.trace.run_id,
                    step=step,
                    pre_fingerprint=intent.fingerprint,
                )

        session.lifecycle.update(
            status=TaskRunStatus.WAITING_APPROVAL,
            current_step=step,
            last_tool=tool_call.name,
            resume_hint="Approve this tool action or rerun with a safer task.",
        )
        approved = (
            self.config.auto_approve_writes
            if approval is None
            else approval.status == "approved"
        )

        if (
            intent.side_effect
            and approval is not None
            and approval.status == "approved"
            and approval.operation_fingerprint is not None
            and not self._same_operation_fingerprint(
                intent.fingerprint,
                approval.operation_fingerprint,
            )
        ):
            stale = self.approval_store.mark_stale(
                approval.operation_key,
                "target fingerprint changed after approval request",
            )
            self.trace.add(
                step,
                session.agent_name,
                "human_approval",
                observation="approval_stale",
                approval_request=stale.to_dict(),
                current_fingerprint=intent.fingerprint,
            )
            return GateResult(
                False,
                StopRequest(
                    status=TaskRunStatus.WAITING_APPROVAL,
                    reason="approval_stale",
                    final_answer=(
                        f"approval_stale: {tool_call.name} approval target changed before execution. "
                        f"operation_key={approval.operation_key} request={approval.path}"
                    ),
                    current_step=step,
                    last_tool=tool_call.name,
                    resume_hint=(
                        "Rerun the task to create a fresh approval request for the current target state."
                    ),
                ),
            )

        if intent.side_effect and approved:
            self.operation_ledger.record_approved(
                intent.key,
                run_id=self.trace.run_id,
                step=step,
            )
        approval_trace = (
            approval.to_dict()
            if approval is not None
            else {
                "operation_key": intent.key,
                "status": "auto_approved",
                "tool_name": tool_call.name,
                "arguments": tool_call.arguments or {},
                "action": intent.action,
            }
        )
        approval_observation = (
            approval.status if approval is not None else "auto_approved"
        )
        self.trace.add(
            step,
            session.agent_name,
            "human_approval",
            observation="approved" if approved else approval_observation,
            approval_request=approval_trace,
        )

        if (
            approval is not None
            and approval.status == "pending"
            and not self.config.auto_approve_writes
        ):
            return GateResult(
                False,
                StopRequest(
                    status=TaskRunStatus.WAITING_APPROVAL,
                    reason="waiting_approval",
                    final_answer=(
                        f"waiting_approval: {tool_call.name} requires approval before execution. "
                        f"operation_key={approval.operation_key} request={approval.path}"
                    ),
                    current_step=step,
                    last_tool=tool_call.name,
                    resume_hint=(
                        f"Run `forge approve {approval.operation_key}` then resume or rerun the task."
                    ),
                ),
            )

        if not approved:
            session.blocked = True
            observation = Observation(
                tool_call.name,
                False,
                f"{tool_call.name}: human approval rejected",
            )
            self._append_feedback(session, tool_call, observation, step)
            session.lifecycle.update(
                status=TaskRunStatus.WAITING_APPROVAL,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=observation.content,
                resume_hint=(
                    "Human approval was rejected; rerun after narrowing the requested edit."
                ),
            )
            return GateResult(False)

        session.lifecycle.update(
            status=TaskRunStatus.RUNNING,
            current_step=step,
        )
        return GateResult(True)

    # 实际执行与证据落盘。治理分支没有通过时不会进入这里。
    def _run_tool(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        step: int,
    ) -> StopRequest | None:
        """通过 registry 执行工具，并写入 evidence、recovery 和 budget。"""

        if intent.side_effect and self.operation_ledger.get(intent.key) is None:
            self.operation_ledger.ensure_planned(
                intent.key,
                tool_call.name,
                tool_call.arguments or {},
                intent.action,
                self.config.workspace,
                run_id=self.trace.run_id,
                step=step,
                status="approved",
                pre_fingerprint=intent.fingerprint,
            )

        hook_context = HookContext(
            run_id=self.trace.run_id,
            step=step,
            agent_name=session.agent_name,
            tool_name=tool_call.name,
            arguments=tool_call.arguments or {},
            action=intent.action,
            command=intent.command,
            auto_approve_writes=self.config.auto_approve_writes,
            approval_mode=getattr(self.config, "approval_mode", "trusted"),
        )
        observation = self.registry.execute(tool_call.name, tool_call.arguments)
        observation = self.hooks.post_tool(hook_context, observation)

        if intent.side_effect:
            self._record_operation_result(tool_call, intent, observation, session, step)

        session.memory.add_observation(observation)
        evidence_item = session.evidence.add_observation(observation)
        validation = self._validation_evidence(
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

        self.trace.add(
            step,
            session.agent_name,
            "tool_call",
            tool_call=tool_call.name,
            tool_arguments=tool_call.arguments,
        )
        self.trace.add(
            step,
            session.agent_name,
            "tool_observation",
            success=observation.success,
            observation=observation.content,
        )
        self.trace.add(
            step,
            session.agent_name,
            "observation",
            success=observation.success,
            observation_summary=observation.content[:300],
        )
        if evidence_item:
            self.trace.add(
                step,
                session.agent_name,
                "evidence_collected",
                evidence=evidence_item.citation(),
            )

        session.observations.append(observation)
        session.lifecycle.update(
            status=TaskRunStatus.RUNNING,
            current_step=step,
            last_tool=tool_call.name,
            last_observation=observation.content[:600],
            messages_count=len(session.messages),
            observations_count=len(session.observations),
        )

        self._record_recovery(session, observation, step, remember=True)
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

    # 第三遍：局部记账、格式转换和纯判断 helper。
    def _record_operation_result(
        self,
        tool_call: ToolCall,
        intent: OperationIntent,
        observation: Observation,
        session: AgentRunSession,
        step: int,
    ) -> None:
        """把副作用执行结果写入幂等账本。"""

        post_fingerprint = OperationLedgerStore.operation_fingerprint(
            tool_call.name,
            tool_call.arguments or {},
            self.config.workspace,
            intent.action,
        )
        if observation.success:
            record = self.operation_ledger.record_executed(
                intent.key,
                run_id=self.trace.run_id,
                step=step,
                observation=observation.content[:600],
                post_fingerprint=post_fingerprint,
            )
        else:
            record = self.operation_ledger.record_failed(
                intent.key,
                run_id=self.trace.run_id,
                step=step,
                observation=observation.content[:600],
                post_fingerprint=post_fingerprint,
            )
        self.trace.add(
            step,
            session.agent_name,
            "operation_ledger",
            operation_key=intent.key,
            operation_status=record.status,
            operation=record.to_dict(),
        )

    def _append_feedback(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        observation: Observation,
        step: int,
    ) -> None:
        """把未执行或被治理的动作反馈给下一轮模型。"""

        session.memory.add_observation(observation)
        session.messages.append(
            Message(
                "tool",
                observation.content,
                name=tool_call.name,
                tool_call_id=tool_call.id,
            )
        )
        self.trace.add(
            step,
            session.agent_name,
            "tool_observation",
            success=observation.success,
            observation=observation.content,
        )

    def _record_recovery(
        self,
        session: AgentRunSession,
        observation: Observation,
        step: int,
        *,
        remember: bool = False,
    ) -> FailureSignal | None:
        """分类失败并把恢复建议写入 trace。"""

        signal = session.controller.classify_observation(observation)
        if signal is None:
            return None
        if remember:
            session.memory.add(f"recovery:{signal.kind.value}:{signal.recovery_hint}")
        self.trace.add(
            step,
            session.agent_name,
            "recovery_decision",
            success=signal.retryable,
            failure_kind=signal.kind.value,
            retryable=signal.retryable,
            recovery_hint=signal.recovery_hint,
        )
        return signal

    def _validation_evidence(
        self,
        tool_name: str,
        arguments: ToolArguments,
        observation: Observation,
    ) -> JsonObject | None:
        """只把测试结果视为 correctness validation。"""

        kind = ""
        if (
            tool_name == "diagnostics"
            and str(arguments.get("kind") or "").lower() == "unittest"
        ):
            kind = "unittest"
        elif tool_name == "run_command":
            try:
                parts = shlex.split(str(arguments.get("command") or ""))
            except ValueError:
                parts = []
            if parts and parts[0].lower() == "pytest":
                kind = "pytest"
            elif len(parts) >= 3 and parts[1:3] in [
                ["-m", "pytest"],
                ["-m", "unittest"],
            ]:
                kind = parts[2].lower()
        if not kind:
            return None

        lowered = observation.content.lower()
        unavailable = any(
            marker in lowered
            for marker in ["validation_blocked", "missing dependency", "no module named"]
        )
        status = (
            "unavailable"
            if unavailable
            else "passed"
            if observation.success
            else "failed"
        )
        return {
            "kind": kind,
            "status": status,
            "tool": tool_name,
            "evidence": observation.content[:600],
        }

    def _message_tool_call(self, call: ToolCall) -> dict[str, Any]:
        """转换为 OpenAI-compatible assistant message 结构。"""

        return {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": json.dumps(call.arguments, ensure_ascii=False),
            },
        }

    def _permission_action(self, tool_name: str) -> str:
        if tool_name == "run_command":
            return "run_command"
        if tool_name in {"apply_patch", "write_file"}:
            return "apply_patch"
        return "read"

    def _is_side_effect_action(self, action: str) -> bool:
        return action in {"apply_patch", "write", "run_command"}

    def _is_recoverable_repeated_tool(self, tool_name: str) -> bool:
        return tool_name in {
            "read_file",
            "grep",
            "grep_search",
            "list_files",
            "git_status",
            "git_diff",
            "diagnostics",
        }

    def _same_operation_fingerprint(
        self,
        left: dict[str, Any] | None,
        right: dict[str, Any] | None,
    ) -> bool:
        return left is not None and right is not None and left == right

"""工具执行前的 hook policy 与人工审批门禁。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_forge.runtime.application.operation_tracker import (
    OperationIntent,
    OperationTracker,
)
from agent_forge.runtime.application.run_lifecycle import StopRequest
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.application.tool_feedback import ToolFeedback
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import Observation, ToolCall
from agent_forge.runtime.domain.governance import HookContext, HookDecisionType
from agent_forge.runtime.domain.task import TaskRunStatus
from agent_forge.runtime.ports import ApprovalRepository, EventSink, HookPort


@dataclass(frozen=True)
class GateResult:
    """工具通过策略链后的下一步。"""

    proceed: bool
    stop: StopRequest | None = None


class ToolAuthorizationGate:
    """统一执行 ALLOW/DENY/ASK 策略，并维护审批状态。"""

    def __init__(
        self,
        config: RuntimeConfig,
        trace: EventSink,
        hooks: HookPort,
        approvals: ApprovalRepository,
        operations: OperationTracker,
        feedback: ToolFeedback,
    ) -> None:
        self.config = config
        self.trace = trace
        self.hooks = hooks
        self.approvals = approvals
        self.operations = operations
        self.feedback = feedback

    # 主要入口：下方定义承接该模块的核心调用。
    def authorize(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        step: int,
    ) -> GateResult:
        """执行 hook，并把 DENY/ASK 映射为确定性治理分支。"""

        hook_context = self._hook_context(session, tool_call, intent, step)
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
            return self._deny(session, tool_call, hook_result.reason, step)
        if hook_result.decision == HookDecisionType.ASK:
            return self._resolve_approval(
                session,
                tool_call,
                intent,
                reason=hook_result.reason,
                step=step,
            )
        return GateResult(True)

    def post_process(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        observation: Observation,
        step: int,
    ) -> Observation:
        """执行与 pre-tool 相同上下文下的 post-tool hook。"""

        return self.hooks.post_tool(
            self._hook_context(session, tool_call, intent, step),
            observation,
        )

    def _deny(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        reason: str,
        step: int,
    ) -> GateResult:
        session.blocked = True
        observation = Observation(tool_call.name, False, f"blocked: {reason}")
        self.feedback.append(session, tool_call, observation, step)
        signal = self.feedback.record_recovery(session, observation, step)
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

        self.operations.ensure_planned(tool_call, intent, step=step)
        approval = self.approvals.get(intent.key)
        if approval is None and not self.config.auto_approve_writes:
            approval = self.approvals.request(
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
                self.operations.record_pending(tool_call, intent, step=step)

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
            and not self.operations.same_fingerprint(
                intent.fingerprint,
                approval.operation_fingerprint,
            )
        ):
            stale = self.approvals.mark_stale(
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
            self.operations.record_approved(intent, step=step)
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
            return self._rejected(session, tool_call, step)

        session.lifecycle.update(status=TaskRunStatus.RUNNING, current_step=step)
        return GateResult(True)

    def _rejected(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        step: int,
    ) -> GateResult:
        session.blocked = True
        observation = Observation(
            tool_call.name,
            False,
            f"{tool_call.name}: human approval rejected",
        )
        self.feedback.append(session, tool_call, observation, step)
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

    def _hook_context(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        step: int,
    ) -> HookContext:
        return HookContext(
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

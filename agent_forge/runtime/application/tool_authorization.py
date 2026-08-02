"""工具执行前的 hook policy 与人工审批门禁。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_forge.runtime.application.operation_tracker import (
    OperationIntent,
    OperationTracker,
)
from agent_forge.runtime.application.run_lifecycle import StopRequest
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.application.tool_feedback import ToolFeedback
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import Observation, ToolCall
from agent_forge.runtime.domain.approval import ApprovalRequestDraft
from agent_forge.runtime.domain.governance import (
    HookContext,
    HookDecisionType,
    HookResult,
)
from agent_forge.runtime.domain.task import TaskCheckpointUpdate, TaskRunStatus
from agent_forge.runtime.ports import ApprovalRepository, EventSink, HookPort


@dataclass(frozen=True, kw_only=True)
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
        operation_tracker: OperationTracker,
        tool_feedback: ToolFeedback,
    ) -> None:
        self.config = config
        self.trace = trace
        self.hooks = hooks
        self.approvals = approvals
        self.operation_tracker = operation_tracker
        self.tool_feedback = tool_feedback

    # 主要入口：合并 Hook、审批和指纹检查，决定工具能否执行。
    def authorize(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        step: int,
    ) -> GateResult:
        """在真实副作用之前合并 Hook、审批和目标指纹决定。

        规范上游是 ``ToolExecutionPipeline``；ALLOW 后的下一 owner 才是
        ``ToolGateway``，ASK/DENY 则返回可持久化的治理结果。每次 hook、permission
        与 approval 决定都进入 trace。系统不变量是模型不能绕过门禁，历史批准也
        不能授权已发生指纹漂移的目标。
        """

        # region 1. Hook 决策：把工具意图映射到统一治理上下文
        hook_context = self._hook_context(session, tool_call, intent, step)
        hook_result = self.hooks.before_tool(hook_context)
        self._record_authorization_decision(
            session=session,
            tool_call=tool_call,
            hook_result=hook_result,
            step=step,
        )
        # endregion 1. Hook 决策结束

        # region 2. 决策分流：DENY 回填失败，ASK 进入审批，ALLOW 直接放行
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
        return GateResult(proceed=True)
        # endregion 2. 决策分流结束

    def apply_after_tool_hooks(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        observation: Observation,
        step: int,
    ) -> Observation:
        """在同一个授权上下文中执行工具后置 Hook 并返回最终 Observation。"""

        return self.hooks.after_tool(
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
        denied_tool_observation = Observation(
            tool_name=tool_call.name,
            success=False,
            content=f"blocked: {reason}",
        )
        self.tool_feedback.append_tool_observation(
            session,
            tool_call,
            denied_tool_observation,
            step,
        )
        recovery_signal = self.tool_feedback.record_recovery_decision(
            session,
            denied_tool_observation,
            step,
        )
        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(
                status=TaskRunStatus.BLOCKED,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=denied_tool_observation.content,
                resume_hint=(
                    recovery_signal.recovery_hint
                    if recovery_signal is not None
                    else "Action was blocked by runtime policy."
                ),
            )
        )
        return GateResult(proceed=False)

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

        # region 1. 建立审批请求：副作用先进入 planned/pending，进程退出后仍可恢复
        self.operation_tracker.ensure_planned(intent, step=step)
        approval = self.approvals.get(intent.operation_key)
        if approval is None and not self.config.auto_approve_writes:
            approval = self.approvals.request(
                ApprovalRequestDraft(
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments or {},
                    action=intent.action,
                    command=intent.command,
                    workspace=self.config.workspace,
                    run_id=self.trace.run_id,
                    step=step,
                    agent_name=session.agent_name,
                    reason=reason,
                    operation_fingerprint=intent.pre_execution_fingerprint,
                )
            )
            if intent.side_effect:
                self.operation_tracker.record_pending(intent, step=step)
        # endregion 1. 建立审批请求结束

        # region 2. 保存等待位置，并计算当前审批是否允许继续
        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(
                status=TaskRunStatus.WAITING_APPROVAL,
                current_step=step,
                last_tool=tool_call.name,
                resume_hint="Approve this tool action or rerun with a safer task.",
            )
        )
        approved = (
            self.config.auto_approve_writes
            if approval is None
            else approval.status == "approved"
        )
        # endregion 2. 保存等待位置结束

        # region 3. TOCTOU 防护：批准后的目标指纹变化会使旧批准失效
        if (
            intent.side_effect
            and approval is not None
            and approval.status == "approved"
            and approval.operation_fingerprint is not None
            and not self.operation_tracker.same_fingerprint(
                intent.pre_execution_fingerprint,
                approval.operation_fingerprint,
            )
        ):
            stale = self.approvals.mark_stale(
                approval.operation_key,
                "target fingerprint changed after approval request",
            )
            self._record_approval_evidence(
                session=session,
                step=step,
                observation="approval_stale",
                approval_request=stale.to_dict(),
                current_fingerprint=intent.pre_execution_fingerprint,
            )
            return GateResult(
                proceed=False,
                stop=StopRequest(
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
        # endregion 3. TOCTOU 防护结束

        # region 4. 审批收口：记录决定，并返回等待、拒绝或继续三种结果
        if intent.side_effect and approved:
            self.operation_tracker.record_approved(intent, step=step)
        approval_trace = (
            approval.to_dict()
            if approval is not None
            else {
                "operation_key": intent.operation_key,
                "status": "auto_approved",
                "tool_name": tool_call.name,
                "arguments": tool_call.arguments or {},
                "action": intent.action,
            }
        )
        approval_observation = (
            approval.status if approval is not None else "auto_approved"
        )
        self._record_approval_evidence(
            session=session,
            step=step,
            observation="approved" if approved else approval_observation,
            approval_request=approval_trace,
        )

        if (
            approval is not None
            and approval.status == "pending"
            and not self.config.auto_approve_writes
        ):
            return GateResult(
                proceed=False,
                stop=StopRequest(
                    status=TaskRunStatus.WAITING_APPROVAL,
                    reason="waiting_approval",
                    final_answer=(
                        f"waiting_approval: {tool_call.name} requires approval before execution. "
                        f"operation_key={approval.operation_key} request={approval.path}"
                    ),
                    current_step=step,
                    last_tool=tool_call.name,
                    resume_hint=(
                        "Run `forge resume <run_dir> --decision approved "
                        f"--operation-key {approval.operation_key}`."
                    ),
                ),
            )

        if not approved:
            return self._rejected(session, tool_call, step)

        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(status=TaskRunStatus.RUNNING, current_step=step)
        )
        return GateResult(proceed=True)
        # endregion 4. 审批收口结束

    def _rejected(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        step: int,
    ) -> GateResult:
        session.blocked = True
        rejected_approval_observation = Observation(
            tool_name=tool_call.name,
            success=False,
            content=f"{tool_call.name}: human approval rejected",
        )
        self.tool_feedback.append_tool_observation(
            session,
            tool_call,
            rejected_approval_observation,
            step,
        )
        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(
                status=TaskRunStatus.WAITING_APPROVAL,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=rejected_approval_observation.content,
                resume_hint=(
                    "Human approval was rejected; rerun after narrowing the requested edit."
                ),
            )
        )
        return GateResult(proceed=False)

    # region 证据记录器（首次阅读可折叠）
    def _record_authorization_decision(
        self,
        *,
        session: AgentRunSession,
        tool_call: ToolCall,
        hook_result: HookResult,
        step: int,
    ) -> None:
        """分别保留 Hook 原始决定和 Runtime 最终权限判断。"""

        self.trace.add(
            step,
            session.agent_name,
            "hook_check",
            hook_stage="before_tool",
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

    def _record_approval_evidence(
        self,
        *,
        session: AgentRunSession,
        step: int,
        observation: str,
        approval_request: dict,
        current_fingerprint: dict | None = None,
    ) -> None:
        """记录审批状态；只在 stale 时附加当前目标指纹。"""

        evidence: dict[str, Any] = {
            "observation": observation,
            "approval_request": approval_request,
        }
        if current_fingerprint is not None:
            evidence["current_fingerprint"] = current_fingerprint
        self.trace.add(
            step,
            session.agent_name,
            "human_approval",
            **evidence,
        )

    # endregion 证据记录器结束

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
            approval_mode=self.config.approval_mode,
        )

"""副作用工具的稳定身份、幂等重放与执行账本。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.application.tool_feedback import ToolFeedback
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import Observation, ToolCall
from agent_forge.runtime.domain.operation import (
    OperationPlan,
    OperationTarget,
    OperationTransition,
)
from agent_forge.runtime.domain.task import TaskCheckpointUpdate, TaskRunStatus
from agent_forge.runtime.ports import EventSink, OperationLedgerRepository


@dataclass(frozen=True)
class OperationIntent:
    """一个工具调用在权限和幂等层面的身份。"""

    action: str
    command: str
    side_effect: bool
    target: OperationTarget
    key: str = ""
    fingerprint: dict[str, Any] | None = None


class OperationTracker:
    """维护副作用操作的 planned -> approved -> executed 状态链。"""

    def __init__(
        self,
        config: RuntimeConfig,
        trace: EventSink,
        operations: OperationLedgerRepository,
        feedback: ToolFeedback,
    ) -> None:
        self.config = config
        self.trace = trace
        self.operations = operations
        self.feedback = feedback

    # 主要入口：把 ToolCall 转成稳定 operation key、权限动作与目标指纹。
    def describe(self, tool_call: ToolCall) -> OperationIntent:
        """把模型 ToolCall 归一化为权限与幂等层共享的稳定意图。

        规范上游是 ``ToolExecutionPipeline``；下一 owner 是授权门禁和 operation
        ledger。只读操作不创建 key，副作用同时绑定 workspace、参数与目标指纹。
        系统不变量是授权、执行和恢复必须使用同一个 ``OperationIntent`` 身份。
        """

        action = self._permission_action(tool_call.name)
        command = str((tool_call.arguments or {}).get("command", ""))
        side_effect = self._is_side_effect_action(action)
        target = OperationTarget(
            tool_name=tool_call.name,
            arguments=tool_call.arguments or {},
            action=action,
            workspace=self.config.workspace,
        )
        if not side_effect:
            return OperationIntent(action, command, False, target)
        return OperationIntent(
            action=action,
            command=command,
            side_effect=True,
            target=target,
            key=self.operations.operation_key(target),
            fingerprint=self.operations.operation_fingerprint(target),
        )

    # 核心规则：只重放指纹未漂移的 executed 事实，绝不重复副作用。
    def replay_if_executed(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        step: int,
    ) -> bool:
        """在工具执行前复用已完成事实，或因目标漂移阻止危险重放。

        规范上游是 ``ToolExecutionPipeline``；命中时下一 owner 是 trace、反馈与
        ``RunLifecycle``，未命中时由调用方继续授权。返回 ``True`` 表示本次调用已
        被账本消费。系统不变量是只有 post-fingerprint 仍匹配的 executed 记录才可
        转成成功 Observation，目标变化必须进入可恢复的 BLOCKED 状态。
        """

        existing = self.operations.get(intent.key)
        if existing is None or existing.status != "executed":
            return False

        stale = existing.post_fingerprint is not None and not self.same_fingerprint(
            intent.fingerprint,
            existing.post_fingerprint,
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
            self.feedback.append(session, tool_call, observation, step)
            session.lifecycle.update(
                TaskCheckpointUpdate(
                    status=TaskRunStatus.BLOCKED,
                    current_step=step,
                    last_tool=tool_call.name,
                    last_observation=observation.content,
                    messages_count=len(session.messages),
                    observations_count=len(session.observations),
                    resume_hint="Reread the target before reissuing a side-effect operation.",
                )
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
        self.feedback.append(session, tool_call, observation, step)
        session.lifecycle.update(
            TaskCheckpointUpdate(
                status=TaskRunStatus.RUNNING,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=observation.content,
                messages_count=len(session.messages),
                observations_count=len(session.observations),
            )
        )
        return True

    def ensure_planned(
        self,
        intent: OperationIntent,
        *,
        step: int,
        status: str = "planned",
    ) -> None:
        """确保副作用在审批或执行前已有账本记录。"""

        if not intent.side_effect:
            return
        self.operations.ensure_planned(self._plan(intent, step, status))

    def record_pending(
        self,
        intent: OperationIntent,
        *,
        step: int,
    ) -> None:
        self.operations.record_pending(self._plan(intent, step, "pending"))

    def record_approved(self, intent: OperationIntent, *, step: int) -> None:
        self.operations.record_approved(
            OperationTransition(
                operation_key=intent.key,
                status="approved",
                run_id=self.trace.run_id,
                step=step,
            )
        )

    # 运行时端口：把真实执行结果与 post-fingerprint 提交到唯一幂等账本。
    def record_result(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        observation: Observation,
        step: int,
    ) -> None:
        """在真实工具返回后提交副作用结果和执行后目标指纹。

        规范上游是 ``ToolExecutionPipeline``；下一 owner 是
        ``OperationLedgerRepository``，随后把同一 record 发布为 trace evidence。
        系统不变量是成功与失败都必须落账，恢复逻辑不能仅凭模型文本判断操作是否
        已执行。
        """

        post_fingerprint = self.operations.operation_fingerprint(intent.target)
        update = OperationTransition(
            operation_key=intent.key,
            status="executed" if observation.success else "failed",
            run_id=self.trace.run_id,
            step=step,
            observation=observation.content[:600],
            post_fingerprint=post_fingerprint,
        )
        if observation.success:
            record = self.operations.record_executed(update)
        else:
            record = self.operations.record_failed(update)
        self.trace.add(
            step,
            session.agent_name,
            "operation_ledger",
            operation_key=intent.key,
            operation_status=record.status,
            operation=record.to_dict(),
        )

    def exists(self, intent: OperationIntent) -> bool:
        return self.operations.get(intent.key) is not None

    def _plan(
        self,
        intent: OperationIntent,
        step: int,
        status: str,
    ) -> OperationPlan:
        return OperationPlan(
            operation_key=intent.key,
            target=intent.target,
            run_id=self.trace.run_id,
            step=step,
            status=status,
            pre_fingerprint=intent.fingerprint,
        )

    @staticmethod
    def same_fingerprint(
        left: dict[str, Any] | None,
        right: dict[str, Any] | None,
    ) -> bool:
        return left is not None and right is not None and left == right

    @staticmethod
    def _permission_action(tool_name: str) -> str:
        if tool_name == "run_command":
            return "run_command"
        if tool_name in {"apply_patch", "write_file"}:
            return "apply_patch"
        return "read"

    @staticmethod
    def _is_side_effect_action(action: str) -> bool:
        return action in {"apply_patch", "write", "run_command"}

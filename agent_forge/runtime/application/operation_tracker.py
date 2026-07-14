"""副作用工具的稳定身份、幂等重放与执行账本。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.application.tool_feedback import ToolFeedback
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import Observation, ToolCall
from agent_forge.runtime.domain.task import TaskRunStatus
from agent_forge.runtime.ports import EventSink, OperationLedgerRepository


@dataclass(frozen=True)
class OperationIntent:
    """一个工具调用在权限和幂等层面的身份。"""

    action: str
    command: str
    side_effect: bool
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

    # PRIMARY ENTRYPOINT: assign stable identity to a tool operation.
    def describe(self, tool_call: ToolCall) -> OperationIntent:
        """把工具名与参数转换为权限动作、稳定 key 和目标指纹。"""

        action = self._permission_action(tool_call.name)
        command = str((tool_call.arguments or {}).get("command", ""))
        side_effect = self._is_side_effect_action(action)
        if not side_effect:
            return OperationIntent(action, command, False)
        return OperationIntent(
            action=action,
            command=command,
            side_effect=True,
            key=self.operations.operation_key(
                tool_call.name,
                tool_call.arguments or {},
                self.config.workspace,
                action,
            ),
            fingerprint=self.operations.operation_fingerprint(
                tool_call.name,
                tool_call.arguments or {},
                self.config.workspace,
                action,
            ),
        )

    def replay_if_executed(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        step: int,
    ) -> bool:
        """跳过可证明已执行的操作；目标变化时阻止错误重放。"""

        existing = self.operations.get(intent.key)
        if existing is None or existing.status != "executed":
            return False

        stale = (
            existing.post_fingerprint is not None
            and not self.same_fingerprint(
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
            self.feedback.append(session, tool_call, observation, step)
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
        self.feedback.append(session, tool_call, observation, step)
        session.lifecycle.update(
            status=TaskRunStatus.RUNNING,
            current_step=step,
            last_tool=tool_call.name,
            last_observation=observation.content,
            messages_count=len(session.messages),
            observations_count=len(session.observations),
        )
        return True

    def ensure_planned(
        self,
        tool_call: ToolCall,
        intent: OperationIntent,
        *,
        step: int,
        status: str = "planned",
    ) -> None:
        """确保副作用在审批或执行前已有账本记录。"""

        if not intent.side_effect:
            return
        self.operations.ensure_planned(
            intent.key,
            tool_call.name,
            tool_call.arguments or {},
            intent.action,
            self.config.workspace,
            run_id=self.trace.run_id,
            step=step,
            status=status,
            pre_fingerprint=intent.fingerprint,
        )

    def record_pending(
        self,
        tool_call: ToolCall,
        intent: OperationIntent,
        *,
        step: int,
    ) -> None:
        self.operations.record_pending(
            intent.key,
            tool_call.name,
            tool_call.arguments or {},
            intent.action,
            self.config.workspace,
            run_id=self.trace.run_id,
            step=step,
            pre_fingerprint=intent.fingerprint,
        )

    def record_approved(self, intent: OperationIntent, *, step: int) -> None:
        self.operations.record_approved(
            intent.key,
            run_id=self.trace.run_id,
            step=step,
        )

    def record_result(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        observation: Observation,
        step: int,
    ) -> None:
        """把副作用执行结果写入幂等账本。"""

        post_fingerprint = self.operations.operation_fingerprint(
            tool_call.name,
            tool_call.arguments or {},
            self.config.workspace,
            intent.action,
        )
        if observation.success:
            record = self.operations.record_executed(
                intent.key,
                run_id=self.trace.run_id,
                step=step,
                observation=observation.content[:600],
                post_fingerprint=post_fingerprint,
            )
        else:
            record = self.operations.record_failed(
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

    def exists(self, intent: OperationIntent) -> bool:
        return self.operations.get(intent.key) is not None

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

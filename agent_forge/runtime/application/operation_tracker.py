"""副作用工具的稳定身份、幂等重放与执行账本。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_forge.contracts import WORKSPACE_WRITE_TOOL_NAMES
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.application.run_lifecycle import StopRequest
from agent_forge.runtime.application.tool_feedback import ToolFeedback
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import Observation, ToolCall
from agent_forge.runtime.domain.operation import (
    OperationPlan,
    OperationRecord,
    OperationTarget,
    OperationTransition,
)
from agent_forge.runtime.domain.task import TaskCheckpointUpdate, TaskRunStatus
from agent_forge.runtime.ports import EventSink, OperationLedgerRepository


@dataclass(frozen=True, kw_only=True)
class OperationIntent:
    """原始 ToolCall 经过 Runtime 归一化后的操作身份。

    这个对象不执行工具。它把授权、Ledger 和真实执行需要共享的事实集中起来，
    避免三个阶段分别解释模型参数，得到不同的“同一次操作”。
    """

    # 权限系统认识的动作类型，例如 read、validate、write、run_command。
    action: str
    # 仅 run_command 使用；授权策略需要单独检查命令文本。
    command: str
    # True 表示操作会形成需要防重放的持久状态变化，必须经过 Ledger 和副作用授权。
    # 验证工具可能在隔离工作区生成可丢弃缓存，但不属于这个 durable side-effect 分类。
    side_effect: bool
    # 生成稳定 key 和目标指纹所需的工具、参数、工作区事实。
    target: OperationTarget
    # 需要 durable side-effect Ledger 的操作才有稳定身份；其他调用保持空字符串。
    operation_key: str = ""
    # 工具执行前目标的当前状态，例如文件路径、SHA256 和大小。
    pre_execution_fingerprint: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class ExistingOperationResolution:
    """恢复时对既有副作用记录作出的唯一决定。

    ``handled_without_execution`` 表示 Ledger 已经给出确定事实，本次不能再次越过真实
    工具边界；``stop_request`` 非空表示既有状态不安全，需要由 ``AgentLoop`` 以显式
    BLOCKED 状态结束本次 continuation。
    """

    handled_without_execution: bool
    stop_request: StopRequest | None = None


class OperationTracker:
    """把 ToolCall 接入副作用身份、幂等重放和持久化状态链。

    本类不拥有工具执行和人工交互。``ToolExecutionPipeline`` 先调用
    ``build_operation_intent`` 建立统一身份，再把同一个 intent 交给 Ledger、
    ``ToolAuthorizationGate`` 和真实工具执行路径。
    """

    def __init__(
        self,
        config: RuntimeConfig,
        trace: EventSink,
        operation_repository: OperationLedgerRepository,
        tool_feedback: ToolFeedback,
    ) -> None:
        self.config = config
        self.trace = trace
        self.operation_repository = operation_repository
        self.tool_feedback = tool_feedback

    # 主要入口：执行任何普通工具前，先把模型输出变成 Runtime 可治理的操作身份。
    def build_operation_intent(self, tool_call: ToolCall) -> OperationIntent:
        """为一个原始 ToolCall 构造授权和 Ledger 共用的 OperationIntent。

        为什么调用：模型只给出工具名和参数，并不知道哪个动作有副作用，也没有稳定
        operation key。Runtime 必须在查询 Ledger、请求审批和执行工具之前，先完成一次
        统一分类，否则三个阶段可能把同一个 ToolCall 解释成不同操作。

        调用位置：``ToolExecutionPipeline._execute_call`` 已完成路由、重复调用和
        ``ask_human`` 分支检查，尚未查询 Ledger、审批或执行真实工具。

        返回结果：只读操作只有 action/target；副作用额外包含由工具、参数、workspace
        和 action 生成的稳定 key，以及执行前目标 fingerprint。
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
            return OperationIntent(
                action=action,
                command=command,
                side_effect=False,
                target=target,
            )
        return OperationIntent(
            action=action,
            command=command,
            side_effect=True,
            target=target,
            operation_key=self.operation_repository.operation_key(target),
            pre_execution_fingerprint=(
                self.operation_repository.operation_fingerprint(target)
            ),
        )

    # 核心规则：只重放确定完成且未漂移的事实；不确定结果必须停止。
    def resolve_existing_operation(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        step: int,
    ) -> ExistingOperationResolution:
        """在工具执行前复用确定事实，或阻止不安全的副作用重放。

        规范上游是 ``ToolExecutionPipeline``；命中时下一 owner 是 trace、反馈与
        ``RunLifecycle``，未命中时由调用方继续授权。系统不变量是只有
        post-fingerprint 仍匹配的 ``executed`` 记录才可转成成功 Observation；
        ``executing`` 表示进程可能在副作用发生后崩溃，必须 fail closed。
        """

        existing_operation_record = self.operation_repository.get(intent.operation_key)
        if existing_operation_record is None:
            return ExistingOperationResolution(handled_without_execution=False)
        if existing_operation_record.status == "executing":
            unknown_outcome_observation = Observation(
                tool_name=tool_call.name,
                success=False,
                content=(
                    "operation_outcome_unknown: a previous process entered the "
                    "side-effect boundary but did not commit its result: "
                    f"{intent.operation_key}"
                ),
            )
            self._record_operation_state(
                step=step,
                agent_name=session.agent_name,
                intent=intent,
                operation_record=existing_operation_record,
                operation_status="operation_outcome_unknown",
                success=False,
                current_fingerprint=intent.pre_execution_fingerprint,
            )
            self.tool_feedback.append_tool_observation(
                session,
                tool_call,
                unknown_outcome_observation,
                step,
            )
            return ExistingOperationResolution(
                handled_without_execution=True,
                stop_request=StopRequest(
                    status=TaskRunStatus.BLOCKED,
                    reason="operation_outcome_unknown",
                    final_answer="blocked: operation outcome is unknown",
                    current_step=step,
                    last_tool=tool_call.name,
                    last_observation=unknown_outcome_observation.content,
                    resume_hint=(
                        "Inspect the operation ledger and target state, then reconcile "
                        "the operation explicitly before starting a fresh attempt."
                    ),
                    metadata={"operation_key": intent.operation_key},
                ),
            )
        if existing_operation_record.status != "executed":
            return ExistingOperationResolution(handled_without_execution=False)

        target_state_drifted = (
            existing_operation_record.post_fingerprint is not None
            and not self.same_fingerprint(
                intent.pre_execution_fingerprint,
                existing_operation_record.post_fingerprint,
            )
        )
        if target_state_drifted:
            stale_record_observation = Observation(
                tool_name=tool_call.name,
                success=False,
                content=(
                    "stale_operation_record: operation was executed before, "
                    f"but target state changed since then: {intent.operation_key}"
                ),
            )
            self._record_operation_state(
                step=step,
                agent_name=session.agent_name,
                intent=intent,
                operation_record=existing_operation_record,
                operation_status="stale_operation_record",
                success=False,
                current_fingerprint=intent.pre_execution_fingerprint,
            )
            self.tool_feedback.append_tool_observation(
                session,
                tool_call,
                stale_record_observation,
                step,
            )
            return ExistingOperationResolution(
                handled_without_execution=True,
                stop_request=StopRequest(
                    status=TaskRunStatus.BLOCKED,
                    current_step=step,
                    reason="stale_operation_record",
                    final_answer="blocked: executed operation target has changed",
                    last_tool=tool_call.name,
                    last_observation=stale_record_observation.content,
                    resume_hint="Reread the target before reissuing a side-effect operation.",
                    messages_count=len(session.messages),
                    observations_count=len(session.observations),
                    metadata={"operation_key": intent.operation_key},
                ),
            )

        # 重放的是上次执行结果这个事实，不是再次调用会修改外部状态的工具。
        prior_execution_fact = (
            existing_operation_record.observation or "operation completed successfully"
        )
        replayed_operation_observation = Observation(
            tool_name=tool_call.name,
            success=True,
            content=(
                "skipped tool execution: ledger already records this operation as "
                f"executed: {intent.operation_key}; "
                f"previous_observation={prior_execution_fact}"
            ),
        )
        self._record_operation_state(
            step=step,
            agent_name=session.agent_name,
            intent=intent,
            operation_record=existing_operation_record,
            operation_status="skipped_already_executed",
        )
        self.tool_feedback.append_tool_observation(
            session,
            tool_call,
            replayed_operation_observation,
            step,
        )
        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(
                status=TaskRunStatus.RUNNING,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=replayed_operation_observation.content,
                messages_count=len(session.messages),
                observations_count=len(session.observations),
            )
        )
        return ExistingOperationResolution(handled_without_execution=True)

    def ensure_planned(
        self,
        intent: OperationIntent,
        *,
        step: int,
        status: str = "planned",
    ) -> None:
        """在副作用进入审批或执行前创建首条 Ledger 记录。

        手动审批路径通常写入 ``planned``；无需人工确认但仍有副作用的路径会在真实执行
        前写入 ``approved``。已有相同 key 时复用记录，不覆盖既有执行事实。
        """

        if not intent.side_effect:
            return
        self.operation_repository.ensure_planned(self._plan(intent, step, status))

    def record_pending(
        self,
        intent: OperationIntent,
        *,
        step: int,
    ) -> None:
        """记录本次副作用正在等待人工审批。"""

        self.operation_repository.record_pending(self._plan(intent, step, "pending"))

    def record_approved(self, intent: OperationIntent, *, step: int) -> None:
        """记录授权门禁已允许这个具体 operation key。"""

        self.operation_repository.record_approved(
            OperationTransition(
                operation_key=intent.operation_key,
                status="approved",
                run_id=self.trace.run_id,
                step=step,
            )
        )

    def record_executing(self, intent: OperationIntent, *, step: int) -> None:
        """在真实副作用调用前提交 durable execution barrier。

        ``executing`` 不宣称成功。它只证明 Runtime 已越过授权边界，后续若进程中断，
        continuation 必须把结果视为未知，而不是再次调用工具。
        """

        executing_operation_record = self.operation_repository.record_executing(
            OperationTransition(
                operation_key=intent.operation_key,
                status="executing",
                run_id=self.trace.run_id,
                step=step,
            )
        )
        self._record_operation_state(
            step=step,
            agent_name="Runtime",
            intent=intent,
            operation_record=executing_operation_record,
            operation_status=executing_operation_record.status,
        )

    # 运行时端口：把真实执行结果与 post-fingerprint 提交到唯一幂等账本。
    def record_execution_result(
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

        post_fingerprint = self.operation_repository.operation_fingerprint(
            intent.target
        )
        execution_result_transition = OperationTransition(
            operation_key=intent.operation_key,
            status="executed" if observation.success else "failed",
            run_id=self.trace.run_id,
            step=step,
            observation=observation.content[:600],
            post_fingerprint=post_fingerprint,
        )
        if observation.success:
            final_operation_record = self.operation_repository.record_executed(
                execution_result_transition
            )
        else:
            final_operation_record = self.operation_repository.record_failed(
                execution_result_transition
            )
        self._record_operation_state(
            step=step,
            agent_name=session.agent_name,
            intent=intent,
            operation_record=final_operation_record,
            operation_status=final_operation_record.status,
        )

    def has_record(self, intent: OperationIntent) -> bool:
        """判断副作用身份是否已经进入 Ledger。"""

        return self.operation_repository.get(intent.operation_key) is not None

    def _record_operation_state(
        self,
        *,
        step: int,
        agent_name: str,
        intent: OperationIntent,
        operation_record: OperationRecord,
        operation_status: str,
        success: bool = True,
        current_fingerprint: dict[str, Any] | None = None,
    ) -> None:
        """把 Ledger 状态投影到 trace；Ledger 本身仍是可恢复状态的权威来源。"""

        evidence: dict[str, Any] = {
            "operation_key": intent.operation_key,
            "operation_status": operation_status,
            "operation": operation_record.to_dict(),
        }
        if current_fingerprint is not None:
            evidence["current_fingerprint"] = current_fingerprint
        self.trace.add(
            step,
            agent_name,
            "operation_ledger",
            success=success,
            **evidence,
        )

    def _plan(
        self,
        intent: OperationIntent,
        step: int,
        operation_status: str,
    ) -> OperationPlan:
        return OperationPlan(
            operation_key=intent.operation_key,
            target=intent.target,
            run_id=self.trace.run_id,
            step=step,
            status=operation_status,
            pre_fingerprint=intent.pre_execution_fingerprint,
        )

    @staticmethod
    def same_fingerprint(
        left: dict[str, Any] | None,
        right: dict[str, Any] | None,
    ) -> bool:
        """仅当两侧都有完整快照且字段完全一致时，才认为目标没有漂移。"""

        return left is not None and right is not None and left == right

    @staticmethod
    def _permission_action(tool_name: str) -> str:
        """把具体工具名收敛成权限和 Ledger 共同理解的动作类型。"""

        if tool_name == "run_command":
            return "run_command"
        if tool_name in WORKSPACE_WRITE_TOOL_NAMES:
            return "write"
        if tool_name == "python_validation":
            return "validate"
        return "read"

    @staticmethod
    def _is_side_effect_action(action: str) -> bool:
        """只让需要审批与幂等保护的持久状态变化进入 Operation Ledger。"""

        return action in {"write", "run_command"}

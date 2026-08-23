"""需审批和防重复保护的持久状态变更操作：身份与操作状态表。"""

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

    这个对象不执行工具。它把授权、操作状态表和真实执行需要共享的事实集中起来，
    避免三个阶段分别解释模型参数，得到不同的“同一次操作”。
    """

    # 权限系统认识的动作类型，例如 read、validate、write、run_command。
    action: str
    # 仅 run_command 使用；授权策略需要单独检查命令文本。
    command: str
    # True 表示这是“需审批和防重复保护的持久状态变更操作”，因此必须经过操作状态表
    # 和授权；它是执行前分类，不表示工具已执行或持久状态已经改变。
    # 验证工具可能在隔离工作区生成可丢弃缓存，但不属于这个 durable 分类。
    side_effect: bool
    # 生成稳定 key 和目标指纹所需的工具、参数、工作区事实。
    target: OperationTarget
    # 需要操作状态表治理的调用才有稳定身份；其他调用保持空字符串。
    operation_key: str = ""
    # 工具执行前目标的当前状态，例如文件路径、SHA256 和大小。
    pre_execution_fingerprint: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class ExistingOperationResolution:
    """恢复时根据操作状态表既有记录作出的唯一决定。

    ``handled_without_execution`` 表示操作状态表已经给出确定事实，本次不能再次越过真实
    工具边界；``stop_request`` 非空表示既有状态不安全，需要由 ``AgentLoop`` 以显式
    BLOCKED 状态结束本次 continuation。
    """

    handled_without_execution: bool
    stop_request: StopRequest | None = None


class OperationTracker:
    """把 ToolCall 接入持久状态变更操作身份、防重复执行和操作状态表。

    本类不拥有工具执行和人工交互。``ToolExecutionPipeline`` 先调用
    ``build_operation_intent`` 建立统一身份，再把同一个 intent 交给操作状态表、
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
        """为一个原始 ToolCall 构造授权和操作状态表共用的 OperationIntent。

        为什么调用：模型只给出工具名和参数，并不知道哪个动作执行后会改变持久状态，也没有稳定
        operation key。Runtime 必须在查询操作状态表、请求审批和执行工具之前，先完成一次
        统一分类，否则三个阶段可能把同一个 ToolCall 解释成不同操作。

        调用位置：``ToolExecutionPipeline._execute_call`` 已完成路由、重复调用和
        ``ask_human`` 分支检查，尚未查询操作状态表、审批或执行真实工具。

        返回结果：只读操作只有 action/target；可能改变持久状态的操作额外包含由工具、参数、workspace
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

    # 核心规则：只回填确定完成且未漂移的执行结果；结果不确定时必须停止。
    def resolve_existing_operation(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        step: int,
    ) -> ExistingOperationResolution:
        """在工具执行前复用确定结果，或阻止不安全的重复执行。

        伪代码：无记录 -> 正常继续；``executing`` -> outcome unknown，fail closed；
        非 ``executed`` -> 交回授权链；``executed`` 但 fingerprint 漂移 -> 阻止复用；
        ``executed`` 且未漂移 -> 回填旧 Observation，不重新执行。

        规范上游是 ``ToolExecutionPipeline``；命中时下一 owner 是 trace、反馈与
        ``RunLifecycle``，未命中时由调用方继续授权。系统不变量是只有
        post-fingerprint 仍匹配的 ``executed`` 记录才可转成成功 Observation；
        ``executing`` 表示进程可能在工具改变外部状态后崩溃，必须 fail closed。
        """

        # region 1. Ledger lookup：无历史事实时把控制权交回正常授权链
        existing_operation_record = self.operation_repository.get(intent.operation_key)
        # Miss 只表示尚无可复用记录，不表示操作已获授权或可以跳过。
        if existing_operation_record is None:
            return ExistingOperationResolution(handled_without_execution=False)
        # endregion 1. Ledger lookup结束

        # region 2. Unknown outcome：executing 可能已经改变外部状态，禁止自动重试
        # executing 不是“仍在后台运行”；它表示进程越过执行边界后没有提交最终结果。
        if existing_operation_record.status == "executing":
            unknown_outcome_observation = Observation(
                tool_name=tool_call.name,
                success=False,
                content=(
                    "operation_outcome_unknown: a previous process entered the "
                    "state-changing operation boundary but did not commit its result: "
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
                    stop_output="blocked: operation outcome is unknown",
                    current_step=step,
                    last_tool=tool_call.name,
                    last_observation=unknown_outcome_observation.content,
                    resume_hint=(
                        "Inspect the operation state record and target state, then reconcile "
                        "the operation explicitly before starting a fresh attempt."
                    ),
                    metadata={"operation_key": intent.operation_key},
                ),
            )
        # endregion 2. Unknown outcome结束

        # region 3. Executed freshness：只有确定完成且目标未漂移的记录可以复用
        # planned/pending/approved/failed 都不是确定成功事实，由后续授权或执行链继续处理。
        if existing_operation_record.status != "executed":
            return ExistingOperationResolution(handled_without_execution=False)

        target_state_drifted = (
            existing_operation_record.post_fingerprint is not None
            and not self.same_fingerprint(
                intent.pre_execution_fingerprint,
                existing_operation_record.post_fingerprint,
            )
        )
        # 目标状态已变化时，旧 executed 事实不再证明当前 ToolCall 的结果。
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
                    stop_output="blocked: executed operation target has changed",
                    last_tool=tool_call.name,
                    last_observation=stale_record_observation.content,
                    resume_hint=(
                        "Reread the target before reissuing a state-changing operation."
                    ),
                    messages_count=len(session.messages),
                    observations_count=len(session.observations),
                    metadata={"operation_key": intent.operation_key},
                ),
            )
        # endregion 3. Executed freshness结束

        # region 4. Deterministic replay：回填既有结果，但绝不再次触达真实工具
        # 回填的是上次执行结果，不会再次调用持久状态变更工具。
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
        # endregion 4. Deterministic replay结束

    def ensure_planned(
        self,
        intent: OperationIntent,
        *,
        step: int,
        status: str = "planned",
    ) -> None:
        """在持久状态变更操作进入审批或执行前创建首条状态记录。

        ``planned`` 只表示“操作意图已登记、工具尚未启动”，不是 LLM 已生成执行计划。
        手动审批路径通常先写入 ``planned``；无需人工确认的状态变更操作会在真实执行
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
        """记录这项可能改变持久状态的操作正在等待人工审批；工具尚未执行。"""

        self.operation_repository.record_pending(self._plan(intent, step, "pending"))

    def record_approved(self, intent: OperationIntent, *, step: int) -> None:
        """记录 Gate 已允许这个 operation key 进入后续执行阶段。

        人工授权事实仍由 ``ApprovalRepository`` 保存；这里更新的是操作状态表。
        """

        self.operation_repository.record_approved(
            OperationTransition(
                operation_key=intent.operation_key,
                status="approved",
                run_id=self.trace.run_id,
                step=step,
            )
        )

    def record_executing(self, intent: OperationIntent, *, step: int) -> None:
        """在真实状态变更操作调用前把操作状态写成 ``executing``。

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

    # 运行时端口：把真实执行结果与 post-fingerprint 提交到唯一操作状态表。
    def record_execution_result(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        intent: OperationIntent,
        observation: Observation,
        step: int,
    ) -> None:
        """在真实工具返回后提交执行结果和执行后目标指纹。

        规范上游是 ``ToolExecutionPipeline``；下一 owner 是
        ``OperationLedgerRepository``（操作状态表仓储端口），随后把同一 record 发布为
        trace evidence。系统不变量是成功与失败都必须写入状态表，恢复逻辑不能仅凭模型文本判断操作是否
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
        """判断这项状态变更操作是否已经进入操作状态表。"""

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
        """把操作状态投影到 trace；操作状态表仍是恢复判断的权威来源。"""

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
        """把具体工具名收敛成权限与操作状态表共同理解的动作类型。"""

        if tool_name == "run_command":
            return "run_command"
        if tool_name == "remember_memory":
            return "memory_write"
        if tool_name == "publish_handoff_event":
            return "coordination_publish"
        if tool_name in WORKSPACE_WRITE_TOOL_NAMES:
            return "write"
        if tool_name == "python_validation":
            return "validate"
        return "read"

    @staticmethod
    def _is_side_effect_action(action: str) -> bool:
        """只让需审批和防重复保护的持久状态变更操作进入操作状态表。"""

        return action in {"write", "run_command", "memory_write"}

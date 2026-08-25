"""模型工具请求的确定性治理管线。

两个核心方法构成执行主链：

1. ``execute_calls``：一次模型响应的公开入口，决定本 model step 真正处理哪些调用。
2. ``_execute_call``：单个调用的主干，按治理顺序把请求送到工具或暂停点。

其余私有方法都是这条主干的叶子规则，不会被外围直接调用。完整链路是：
``选择调用 -> 路由检查 -> HITL 屏障 -> 操作状态 -> 连续重复策略 -> 授权 -> 执行 -> 证据``。
执行授权、操作状态和反馈格式分别由 ``tool_authorization.py``、
``operation_tracker.py`` 和 ``tool_feedback.py`` 拥有。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

from agent_forge.contracts import JsonObject, JsonValue
from agent_forge.memory.domain import MemoryConsolidationAction, MemoryScope
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
from agent_forge.runtime.application.step_control import FailureSignal
from agent_forge.runtime.domain.conversation import (
    AgentResponse,
    Message,
    Observation,
    ToolCall,
)
from agent_forge.runtime.domain.human_input import HumanInputQuestion
from agent_forge.runtime.domain.model import ModelCapabilities
from agent_forge.runtime.domain.thread import ConversationItem, ConversationItemDraft
from agent_forge.runtime.domain.task import (
    PendingExecutionPointer,
    TaskCheckpointUpdate,
    TaskRunStatus,
)
from agent_forge.runtime.ports import (
    ApprovalRepository,
    EventSink,
    HookPort,
    OperationLedgerRepository,
    RunControlPort,
    ToolGateway,
)
from agent_forge.runtime.ports.thread import ConversationThreadRepository
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
    end_batch: bool = False


@dataclass(frozen=True, kw_only=True)
class PendingBatchOutcome:
    """Run 开始时是否恢复了原 assistant batch，以及恢复中是否再次暂停。"""

    resumed: bool
    stop_request: StopRequest | None = None


class ToolExecutionPipeline:
    """把模型工具请求转换为受治理、可恢复的 Observation。

    本类只有 ``execute_calls`` 是外围入口。下划线方法是按执行阶段命名的内部步骤，
    每个步骤只拥有一种决策；它们不构成独立的公共 API。当前所有私有方法
    都由本类主链调用，没有预留但未接线的方法。

    折叠后按下面的纵向顺序读即可：

    ``execute_calls`` -> ``_select_calls_for_model_step`` -> ``_execute_call``

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
        conversation_threads: ConversationThreadRepository,
    ) -> None:
        self.trace = trace
        self.tool_gateway = registry
        configured_tool_calls = max(1, int(config.max_tool_calls_per_turn))
        self.max_tool_calls_per_model_step = (
            configured_tool_calls if model_capabilities.parallel_tool_calls else 1
        )
        self.run_control_handler = RunControlHandler(run_control, trace)
        self.conversation_threads = conversation_threads
        self.tool_feedback = ToolFeedback(trace, conversation_threads)
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

    # 主要入口：治理本 model step 的 ToolCall，在人工屏障或终止处返回 StopRequest。
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

        # region 1. 批次整形：区分模型原始 batch 与本轮可执行子集
        # 截断只改变 Runtime 的执行 disposition，不能删除模型实际提出的 ToolCall；
        # 后续每个原始调用都必须得到唯一的 Tool Observation 或明确的延后结果。
        selected_tool_calls = self._select_calls_for_model_step(
            session,
            response,
            step,
        )
        # Canonical assistant item 保存 provider 返回的完整 batch；预算截断或
        # ask_human 屏障只改变执行 disposition，不能删除模型已经提出的 ToolCall。
        protocol_tool_calls = list(response.tool_calls)
        if len({call.id for call in protocol_tool_calls}) != len(protocol_tool_calls):
            return StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason="invalid_tool_batch",
                stop_output="blocked: assistant tool_call ids must be unique within a batch",
                current_step=step,
            )
        executable_tool_call_ids = {call.id for call in selected_tool_calls}
        assistant_tool_calls = [
            self.tool_feedback.to_message_tool_call(tool_call)
            for tool_call in protocol_tool_calls
        ]
        # 完整 assistant batch 必须先于任一 ToolGateway 调用 durable append。
        # content 不能因同一响应带 ToolCall 而丢失；checkpoint 只保存这个 item 的指针。
        allowed_tool_names_json: list[JsonValue] = []
        allowed_tool_names_json.extend(sorted(allowed_tool_names))
        executable_tool_call_ids_json: list[JsonValue] = []
        executable_tool_call_ids_json.extend(sorted(executable_tool_call_ids))
        # endregion 1. 批次整形结束

        # region 2. 协议持久化：完整 assistant batch 先落盘，再登记 pending cursor
        # ConversationThread.append 与 checkpoint pointer 共同定义恢复入口；任何真实工具
        # 都只能在这两个事实写入之后开始，崩溃恢复因此不依赖模型重新生成调用。
        assistant_item = self.conversation_threads.append(
            session.thread_id,
            ConversationItemDraft(
                item_id=f"assistant:{self.trace.run_id}:{step}",
                turn_id=session.turn_id,
                run_id=self.trace.run_id,
                role="assistant",
                content=response.content or "",
                reasoning_content=response.reasoning_content,
                tool_calls=tuple(assistant_tool_calls),
                metadata={
                    "model_step": step,
                    "allowed_tool_names": allowed_tool_names_json,
                    "executable_tool_call_ids": executable_tool_call_ids_json,
                },
                origin="model_tool_calls",
                human_authority=False,
            ),
        )
        self._mirror_assistant_item(session, assistant_item)
        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(
                status=TaskRunStatus.RUNNING,
                current_step=step,
                pending_execution=PendingExecutionPointer(
                    assistant_item_id=assistant_item.item_id,
                ),
                messages_count=len(session.messages),
            )
        )
        # endregion 2. 协议持久化结束

        # region 3. 顺序执行：每个 ToolCall 都重新经过控制、安全与幂等边界
        # 同一模型响应可以包含多个 ToolCall，但这里故意顺序执行：前一个状态变更操作可能改变
        # 后一个调用的目标指纹，且操作员必须能在每项此类操作启动前 pause/cancel。
        # 按模型原顺序消费 durable batch；cursor 只在对应 Tool Observation 已落盘后推进。
        return self._continue_pending_batch(
            session,
            assistant_item=assistant_item,
            tool_calls=protocol_tool_calls,
            step=step,
            allowed_tool_names=allowed_tool_names,
            executable_tool_call_ids=executable_tool_call_ids,
        )
        # endregion 3. 顺序执行结束

    def resume_pending_calls(self, session: AgentRunSession) -> PendingBatchOutcome:
        """不调用模型，直接从 checkpoint cursor 恢复同 Turn 的原 assistant batch。"""

        # region 1. 恢复入口：优先用 checkpoint pointer；缺失时只修复唯一 orphan batch
        pending = session.lifecycle.checkpoint.pending_execution
        if pending is None:
            unfinished_batches: list[tuple[ConversationItem, int, int]] = []
            for candidate in self.conversation_threads.list_recent_items(
                session.thread_id,
                turn_id=session.turn_id,
                limit=200,
            ):
                if candidate.role != "assistant" or candidate.origin != "model_tool_calls":
                    continue
                try:
                    candidate_calls = self._tool_calls_from_item(candidate)
                except (TypeError, ValueError, json.JSONDecodeError):
                    return PendingBatchOutcome(
                        resumed=True,
                        stop_request=StopRequest(
                            status=TaskRunStatus.BLOCKED,
                            reason="pending_execution_corrupt",
                            stop_output="blocked: malformed orphaned assistant tool batch",
                        ),
                    )
                first_missing = next(
                    (
                        index
                        for index, call in enumerate(candidate_calls)
                        if self.conversation_threads.get_item(
                            session.thread_id,
                            self._tool_item_id(candidate.item_id, index, call.id),
                        )
                        is None
                    ),
                    None,
                )
                if first_missing is not None:
                    raw_step = candidate.metadata.get("model_step")
                    if not isinstance(raw_step, int) or raw_step < 1:
                        return PendingBatchOutcome(
                            resumed=True,
                            stop_request=StopRequest(
                                status=TaskRunStatus.BLOCKED,
                                reason="pending_execution_corrupt",
                                stop_output="blocked: orphaned batch is missing model_step",
                            ),
                        )
                    unfinished_batches.append((candidate, first_missing, raw_step))
            if not unfinished_batches:
                return PendingBatchOutcome(resumed=False)
            if len(unfinished_batches) != 1:
                return PendingBatchOutcome(
                    resumed=True,
                    stop_request=StopRequest(
                        status=TaskRunStatus.BLOCKED,
                        reason="pending_execution_ambiguous",
                        stop_output="blocked: multiple unfinished assistant tool batches",
                    ),
                )
            orphaned_assistant, first_missing, orphaned_step = unfinished_batches[0]
            session.lifecycle.update_checkpoint(
                TaskCheckpointUpdate(
                    status=TaskRunStatus.RUNNING,
                    current_step=orphaned_step,
                    pending_execution=PendingExecutionPointer(
                        assistant_item_id=orphaned_assistant.item_id,
                        next_tool_call_index=first_missing,
                    ),
                )
            )
            pending = session.lifecycle.checkpoint.pending_execution
            if pending is None:  # pragma: no cover - repository transition invariant
                raise AssertionError("pending execution reconcile did not persist pointer")
        # endregion 1. 恢复入口结束

        # region 2. Canonical batch：恢复完整 assistant item 并验证原路由 disposition
        assistant_item = self.conversation_threads.get_item(
            session.thread_id,
            pending.assistant_item_id,
        )
        if (
            assistant_item is None
            or assistant_item.turn_id != session.turn_id
            or assistant_item.role != "assistant"
            or not assistant_item.tool_calls
        ):
            return PendingBatchOutcome(
                resumed=True,
                stop_request=StopRequest(
                    status=TaskRunStatus.BLOCKED,
                    reason="pending_execution_corrupt",
                    stop_output="blocked: pending execution assistant batch is unavailable",
                    current_step=session.lifecycle.checkpoint.current_step,
                    resume_hint="Inspect the ConversationThread journal and checkpoint pointer.",
                ),
            )
        try:
            tool_calls = self._tool_calls_from_item(assistant_item)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            return PendingBatchOutcome(
                resumed=True,
                stop_request=StopRequest(
                    status=TaskRunStatus.BLOCKED,
                    reason="pending_execution_corrupt",
                    stop_output=f"blocked: invalid pending tool batch: {error}",
                    current_step=session.lifecycle.checkpoint.current_step,
                ),
            )
        self._mirror_assistant_item(session, assistant_item)
        raw_allowed = assistant_item.metadata.get("allowed_tool_names")
        raw_executable = assistant_item.metadata.get("executable_tool_call_ids")
        if not isinstance(raw_allowed, list) or not isinstance(raw_executable, list):
            return PendingBatchOutcome(
                resumed=True,
                stop_request=StopRequest(
                    status=TaskRunStatus.BLOCKED,
                    reason="pending_execution_corrupt",
                    stop_output="blocked: pending batch routing metadata is unavailable",
                    current_step=session.lifecycle.checkpoint.current_step,
                ),
            )
        allowed_tool_name_values = [
            value for value in raw_allowed if isinstance(value, str)
        ]
        executable_tool_call_id_values = [
            value for value in raw_executable if isinstance(value, str)
        ]
        if (
            len(allowed_tool_name_values) != len(raw_allowed)
            or len(executable_tool_call_id_values) != len(raw_executable)
        ):
            return PendingBatchOutcome(
                resumed=True,
                stop_request=StopRequest(
                    status=TaskRunStatus.BLOCKED,
                    reason="pending_execution_corrupt",
                    stop_output="blocked: pending batch routing metadata is unavailable",
                    current_step=session.lifecycle.checkpoint.current_step,
                ),
            )
        batch_call_ids = [call.id for call in tool_calls]
        if (
            len(batch_call_ids) != len(set(batch_call_ids))
            or not set(executable_tool_call_id_values).issubset(batch_call_ids)
            or any(
                call.id in executable_tool_call_id_values
                and call.name not in allowed_tool_name_values
                for call in tool_calls
            )
        ):
            return PendingBatchOutcome(
                resumed=True,
                stop_request=StopRequest(
                    status=TaskRunStatus.BLOCKED,
                    reason="pending_execution_corrupt",
                    stop_output="blocked: pending batch disposition is inconsistent",
                    current_step=session.lifecycle.checkpoint.current_step,
                ),
            )
        # endregion 2. Canonical batch 验证结束

        # region 3. 当前能力收窄：Registry 只能撤销旧能力，不能扩大原 Turn 的路由授权
        # 原 Turn 的路由事实被冻结在 assistant item；当前 Registry 只能进一步收窄，
        # 不能在 resume 时把当时隐藏的工具扩大为允许。
        allowed_tool_names = {
            name
            for name in allowed_tool_name_values
            if self.tool_gateway.get(name) is not None
        }
        executable_tool_call_ids = set(executable_tool_call_id_values)
        return PendingBatchOutcome(
            resumed=True,
            stop_request=self._continue_pending_batch(
                session,
                assistant_item=assistant_item,
                tool_calls=tool_calls,
                step=session.lifecycle.checkpoint.current_step,
                allowed_tool_names=allowed_tool_names,
                executable_tool_call_ids=executable_tool_call_ids,
            ),
        )
        # endregion 3. 当前能力收窄与续跑结束

    def _continue_pending_batch(
        self,
        session: AgentRunSession,
        *,
        assistant_item: ConversationItem,
        tool_calls: list[ToolCall],
        step: int,
        allowed_tool_names: set[str],
        executable_tool_call_ids: set[str],
    ) -> StopRequest | None:
        """按 durable cursor 顺序消费 batch；Observation append 成功才推进。"""

        # region 1. Cursor 契约：pointer 必须精确指向当前 assistant batch
        pending = session.lifecycle.checkpoint.pending_execution
        if pending is None or pending.assistant_item_id != assistant_item.item_id:
            raise RuntimeError("pending execution pointer does not match assistant batch")
        if pending.next_tool_call_index > len(tool_calls):
            raise RuntimeError("pending tool call cursor is outside assistant batch")
        # endregion 1. Cursor 契约结束

        # region 2. 顺序消费：回放既有 Observation，或经控制/授权边界执行当前调用
        for tool_call_index in range(pending.next_tool_call_index, len(tool_calls)):
            tool_call = tool_calls[tool_call_index]
            existing_tool_item = self.conversation_threads.get_item(
                session.thread_id,
                self._tool_item_id(
                    assistant_item.item_id,
                    tool_call_index,
                    tool_call.id,
                ),
            )
            # 上次进程已 append Observation、但尚未来得及推进 cursor：只修复 cursor。
            if existing_tool_item is not None:
                self._mirror_tool_item(session, existing_tool_item)
                self._advance_pending_cursor(
                    session,
                    assistant_item_id=assistant_item.item_id,
                    next_tool_call_index=tool_call_index + 1,
                    tool_call_count=len(tool_calls),
                    step=step,
                )
                continue

            # 未进入执行预算的调用仍需一条确定性 Tool Observation，闭合 provider 协议。
            if tool_call.id not in executable_tool_call_ids:
                nonexecution_reason = (
                    "deferred: ask_human is an exclusive barrier; re-propose this "
                    "tool call after the human answer"
                    if any(call.name == "ask_human" for call in tool_calls)
                    else "skipped: tool call exceeded the per-model-step execution budget"
                )
                self.tool_feedback.append_tool_observation(
                    session,
                    tool_call,
                    Observation(
                        tool_name=tool_call.name,
                        success=False,
                        content=nonexecution_reason,
                    ),
                    step,
                )
                self._advance_pending_cursor(
                    session,
                    assistant_item_id=assistant_item.item_id,
                    next_tool_call_index=tool_call_index + 1,
                    tool_call_count=len(tool_calls),
                    step=step,
                )
                continue

            operator_control = self.run_control_handler.consume_pending_signals(
                session,
                step,
                include_model_input_signals=False,
            )
            # Tool 批次中只允许 terminal 打断；模型输入类信号继续等待下一模型边界。
            if operator_control.stop is not None:
                if operator_control.stop.status != TaskRunStatus.PAUSED:
                    self._close_terminal_batch(
                        session,
                        assistant_item_id=assistant_item.item_id,
                        tool_calls=tool_calls,
                        first_index=tool_call_index,
                        step=step,
                        reason=operator_control.stop.reason,
                    )
                return operator_control.stop
            tool_call_outcome = self._execute_call(
                session,
                tool_call,
                step=step,
                allowed_tool_names=allowed_tool_names,
            )
            persisted_tool_item = self.conversation_threads.get_item(
                session.thread_id,
                self._tool_item_id(
                    assistant_item.item_id,
                    tool_call_index,
                    tool_call.id,
                ),
            )
            # 等待 Approval/Human/Pause 时没有 Observation，cursor 必须停在原调用。
            if persisted_tool_item is None:
                if tool_call_outcome.stop_request is not None:
                    if tool_call_outcome.stop_request.status in {
                        TaskRunStatus.WAITING_APPROVAL,
                        TaskRunStatus.WAITING_HUMAN,
                        TaskRunStatus.PAUSED,
                    }:
                        return tool_call_outcome.stop_request
                    self._close_terminal_batch(
                        session,
                        assistant_item_id=assistant_item.item_id,
                        tool_calls=tool_calls,
                        first_index=tool_call_index,
                        step=step,
                        reason=tool_call_outcome.stop_request.reason,
                    )
                    return tool_call_outcome.stop_request
                raise RuntimeError(
                    f"tool call completed without durable observation: {tool_call.id}"
                )
            self._mirror_tool_item(session, persisted_tool_item)
            self._advance_pending_cursor(
                session,
                assistant_item_id=assistant_item.item_id,
                next_tool_call_index=tool_call_index + 1,
                tool_call_count=len(tool_calls),
                step=step,
            )
            # 单个 ToolCall 产生明确停止请求时，Observation 已落盘后再停止。
            if tool_call_outcome.stop_request is not None:
                if tool_call_outcome.stop_request.status not in {
                    TaskRunStatus.WAITING_APPROVAL,
                    TaskRunStatus.WAITING_HUMAN,
                    TaskRunStatus.PAUSED,
                }:
                    self._close_terminal_batch(
                        session,
                        assistant_item_id=assistant_item.item_id,
                        tool_calls=tool_calls,
                        first_index=tool_call_index + 1,
                        step=step,
                        reason=tool_call_outcome.stop_request.reason,
                    )
                return tool_call_outcome.stop_request
            # 人工拒绝使原 batch 后续意图失去前提，直接让下一模型 step 重规划。
            if tool_call_outcome.end_batch:
                self._skip_remaining_batch_calls(
                    session,
                    assistant_item_id=assistant_item.item_id,
                    tool_calls=tool_calls,
                    first_index=tool_call_index + 1,
                    step=step,
                )
                return None
        # endregion 2. 顺序消费结束

        # region 3. Batch 收口：所有调用都有唯一 Observation 后清除 pending pointer
        # 全部调用都有唯一 durable Observation 后，清除 batch pointer。
        if session.lifecycle.checkpoint.pending_execution is not None:
            session.lifecycle.update_checkpoint(
                TaskCheckpointUpdate(
                    status=TaskRunStatus.RUNNING,
                    current_step=step,
                    pending_execution=None,
                    messages_count=len(session.messages),
                    observations_count=len(session.observations),
                )
            )
        return None
        # endregion 3. Batch 收口结束

    def _skip_remaining_batch_calls(
        self,
        session: AgentRunSession,
        *,
        assistant_item_id: str,
        tool_calls: list[ToolCall],
        first_index: int,
        step: int,
    ) -> None:
        """审批拒绝使后续意图前提失效；逐项写 skipped Observation 后清 cursor。"""

        for index in range(first_index, len(tool_calls)):
            call = tool_calls[index]
            if self.conversation_threads.get_item(
                session.thread_id,
                self._tool_item_id(assistant_item_id, index, call.id),
            ) is None:
                self.tool_feedback.append_tool_observation(
                    session,
                    call,
                    Observation(
                        tool_name=call.name,
                        success=False,
                        content=(
                            "skipped: an earlier approval rejection invalidated the "
                            "remaining assistant tool batch"
                        ),
                    ),
                    step,
                )
            self._advance_pending_cursor(
                session,
                assistant_item_id=assistant_item_id,
                next_tool_call_index=index + 1,
                tool_call_count=len(tool_calls),
                step=step,
            )

    def _close_terminal_batch(
        self,
        session: AgentRunSession,
        *,
        assistant_item_id: str,
        tool_calls: list[ToolCall],
        first_index: int,
        step: int,
        reason: str,
    ) -> None:
        """terminal stop 为每个未闭合调用写 Observation，并将 cursor 收到 batch 末尾。"""

        for index in range(first_index, len(tool_calls)):
            call = tool_calls[index]
            item_id = self._tool_item_id(assistant_item_id, index, call.id)
            if self.conversation_threads.get_item(session.thread_id, item_id) is None:
                self.tool_feedback.append_tool_observation(
                    session,
                    call,
                    Observation(
                        tool_name=call.name,
                        success=False,
                        content=(
                            f"not executed: tool batch terminated by {reason}"
                        ),
                    ),
                    step,
                )
            self._advance_pending_cursor(
                session,
                assistant_item_id=assistant_item_id,
                next_tool_call_index=index + 1,
                tool_call_count=len(tool_calls),
                step=step,
            )

    @staticmethod
    def _tool_item_id(
        assistant_item_id: str,
        tool_call_index: int,
        tool_call_id: str,
    ) -> str:
        """把 Thread-global assistant 身份、batch index 与 provider call id 绑定。"""

        return (
            f"tool:{assistant_item_id}:{tool_call_index}:{tool_call_id}"
        )

    def _advance_pending_cursor(
        self,
        session: AgentRunSession,
        *,
        assistant_item_id: str,
        next_tool_call_index: int,
        tool_call_count: int,
        step: int,
    ) -> None:
        """原子更新 batch cursor，并清除上一调用的 Approval 定位字段。"""

        next_pointer = (
            None
            if next_tool_call_index >= tool_call_count
            else PendingExecutionPointer(
                assistant_item_id=assistant_item_id,
                next_tool_call_index=next_tool_call_index,
            )
        )
        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(
                status=TaskRunStatus.RUNNING,
                current_step=step,
                pending_execution=next_pointer,
                messages_count=len(session.messages),
                observations_count=len(session.observations),
            )
        )

    @staticmethod
    def _tool_calls_from_item(assistant_item: ConversationItem) -> list[ToolCall]:
        """把 canonical assistant tool_calls 无损恢复为 Runtime ToolCall。"""

        restored: list[ToolCall] = []
        for raw_call in assistant_item.tool_calls:
            raw_function = raw_call.get("function")
            if not isinstance(raw_function, dict):
                raise ValueError("tool call function payload is missing")
            raw_arguments = raw_function.get("arguments", "{}")
            if isinstance(raw_arguments, str):
                arguments = json.loads(raw_arguments)
            elif isinstance(raw_arguments, dict):
                arguments = dict(raw_arguments)
            else:
                raise ValueError("tool call arguments must be an object or JSON string")
            if not isinstance(arguments, dict):
                raise ValueError("tool call arguments must be an object")
            restored.append(
                ToolCall(
                    id=str(raw_call.get("id") or ""),
                    name=str(raw_function.get("name") or ""),
                    arguments=arguments,
                )
            )
        if any(not call.id or not call.name for call in restored):
            raise ValueError("tool call id/name is missing")
        return restored

    @staticmethod
    def _mirror_assistant_item(
        session: AgentRunSession,
        item: ConversationItem,
    ) -> None:
        if item.sequence in session.message_sequences:
            return
        session.messages.append(
            Message(
                role="assistant",
                content=item.content,
                reasoning_content=item.reasoning_content,
                tool_calls=[dict(call) for call in item.tool_calls],
                item_id=item.item_id,
                turn_id=item.turn_id,
            )
        )
        session.message_sequences.append(item.sequence)

    @staticmethod
    def _mirror_tool_item(session: AgentRunSession, item: ConversationItem) -> None:
        if item.sequence in session.message_sequences:
            return
        session.messages.append(
            Message(
                role="tool",
                content=item.content,
                name=item.name,
                tool_call_id=item.tool_call_id,
                item_id=item.item_id,
                turn_id=item.turn_id,
            )
        )
        session.message_sequences.append(item.sequence)

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

        伪代码：路由与 Guardrail -> ask_human durable barrier
        -> remember_memory provenance -> Operation Ledger replay/fail-closed
        -> repeat limit -> authorization -> 唯一真实执行入口 ``_run_tool``。

        只有前述阶段均允许时才进入 ``_run_tool``；每个拒绝、等待、回填或执行分支都返回
        明确的 ``ToolCallOutcome``，并在对应阶段提交 Observation、Trace 或 Checkpoint。
        """

        # region 1. 调用意图预检：唯一计数器观察连续相同调用，并确认本轮可见性
        # 重复检测回答“模型是否原地打转”；路由复核回答“本轮是否向模型暴露该工具”。
        # 两者都在持久状态变化风险分类和授权前完成，但路由失败优先返回明确 Observation。
        repeat_limit_signal = (
            session.controller.observe_tool_intent_for_repeat_limit(tool_call)
        )
        tool_is_routed_for_this_model_step = (
            self.tool_gateway.get(tool_call.name) is not None
            and tool_call.name in allowed_tool_names
        )
        guardrail_decision = tool_guardrail(
            tool_call.name,
            tool_call.arguments,
            exists=tool_is_routed_for_this_model_step,
        )
        self._record_tool_guardrail(session, step, guardrail_decision)

        # 当前 Model Step 没有向模型暴露该工具时立即失败，不能进入 HITL、Ledger 或 Gateway。
        if not tool_is_routed_for_this_model_step:
            self._handle_unrouted_tool(session, tool_call, step)
            return ToolCallOutcome(
                status=ToolCallStatus.FAILED,
                reason="tool_not_routed_for_this_turn",
            )
        # endregion 1. 调用意图预检结束

        # region 2. 协议分支：记录工具意图，ask_human 转入 durable HITL
        self._record_model_tool_intent(session, step, tool_call)
        # ask_human 是协议特殊分支：建立可恢复人工屏障，不按普通 Tool 执行。
        if tool_call.name == "ask_human":
            return self._handle_human_question(session, tool_call, step)
        # endregion 2. 协议分支结束

        # tool_guardrail 只形成语义检查证据；真正的阻断条件是上面的路由复核结果。

        # region 3. 操作状态表：状态变更操作先复用确定结果，再考虑无进展重复
        # 这里是 OperationLedgerRepository（操作状态表）的唯一入口。ToolCall 只是模型给出的原始工具名和参数；
        # 在查询旧记录、申请权限或真正执行前，必须先得到三者共用的状态变更风险分类、
        # operation key 和执行前目标指纹。无需操作状态表治理的调用也会归一化，
        # 但不会创建 operation record。
        memory_provenance: JsonObject | None = None
        # Memory 写入必须先绑定当前 Session 的 user 原文，模型声明本身不构成授权。
        if tool_call.name == "remember_memory":
            memory_provenance = self._find_user_memory_provenance(
                session,
                tool_call,
            )
            # 找不到精确 user quote 时 fail closed，且不创建 operation record。
            if memory_provenance is None:
                return self._reject_memory_without_provenance(
                    session,
                    tool_call,
                    step,
                )
            memory_validation_error = self._memory_consolidation_validation_error(
                session,
                tool_call,
            )
            if memory_validation_error:
                return self._reject_invalid_memory_consolidation(
                    session,
                    tool_call,
                    step,
                    memory_validation_error,
                )
        operation_intent = self.operation_tracker.build_operation_intent(
            session,
            tool_call,
        )
        # 只有已验证的 quote 才记录为 Memory 授权 provenance。
        if memory_provenance is not None:
            self._record_memory_authorization(
                session=session,
                step=step,
                operation_key=operation_intent.operation_key,
                provenance=memory_provenance,
            )
        # 持久状态变更进入 Ledger 防重与恢复判断。run_command 以 canonical
        # ToolCall invocation 为 key：同一调用 crash resume 不重跑，新调用仍可执行。
        if operation_intent.ledger_tracked:
            existing_operation = self.operation_tracker.resolve_existing_operation(
                session,
                tool_call,
                operation_intent,
                step,
            )
            # unknown outcome 或 stale record 必须停止，不能冒险再次执行。
            if existing_operation.stop_request is not None:
                return ToolCallOutcome(
                    status=ToolCallStatus.STOPPED,
                    reason=existing_operation.stop_request.reason,
                    stop_request=existing_operation.stop_request,
                )
            # 确定执行且未漂移的旧事实只回填 Observation，不再次调用真实工具。
            if existing_operation.handled_without_execution:
                return ToolCallOutcome(
                    status=ToolCallStatus.SKIPPED,
                    reason="replayed_executed_operation_fact",
                )
        # endregion 3. 操作状态表结束

        # region 4. 连续重复策略：无持久状态变化的调用跳过，可能改变持久状态的调用停止
        # Ledger 没有可复用事实后，才按连续 ToolCall 计数处理模型无进展循环。
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
        # WAITING_APPROVAL 或 stale approval 通过 StopRequest 返回，不能进入 Gateway。
        if authorization_decision.stop is not None:
            return ToolCallOutcome(
                status=ToolCallStatus.STOPPED,
                reason=authorization_decision.stop.reason,
                stop_request=authorization_decision.stop,
            )
        # 明确 DENY 已形成失败 Observation；这里终止当前调用，不重复记录事实。
        if not authorization_decision.proceed:
            return ToolCallOutcome(
                status=ToolCallStatus.FAILED,
                reason="tool_authorization_rejected",
                end_batch=authorization_decision.end_batch,
            )
        return self._run_tool(session, tool_call, operation_intent, step)
        # endregion 5. 权限门与真实执行结束

    # region 分支与证据叶子
    def _find_user_memory_provenance(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
    ) -> JsonObject | None:
        """精确匹配当前 Turn 的 human-authority 原文，不推断自然语言语义。"""

        source_quote = tool_call.arguments.get("source_quote")
        if not isinstance(source_quote, str) or not source_quote.strip():
            return None
        normalized_quote = source_quote.strip()
        current_turn_items = self.conversation_threads.list_recent_items(
            session.thread_id,
            turn_id=session.turn_id,
            limit=200,
        )
        for item in reversed(current_turn_items):
            # Provider transport role 不是授权事实；只有 Thread 标记的人类权威输入可授权。
            if item.role != "user" or not item.human_authority:
                continue
            if normalized_quote not in item.content:
                continue
            return {
                "item_id": item.item_id,
                "sequence": item.sequence,
                "item_hash": item.item_hash,
                "source_quote": normalized_quote,
            }
        return None

    @staticmethod
    def _memory_consolidation_validation_error(
        session: AgentRunSession,
        tool_call: ToolCall,
    ) -> str:
        """验证 action 与当前 Turn candidate target；不判断自然语言语义等价。"""

        action = str(tool_call.arguments.get("action") or "").strip().upper()
        valid_actions = {item.value for item in MemoryConsolidationAction}
        if action not in valid_actions:
            return "action must be CREATE, UPDATE, or NOOP"
        key = str(tool_call.arguments.get("key") or "").strip()
        content = str(tool_call.arguments.get("content") or "").strip()
        if not key or not content:
            return "key and content are required"
        scope = str(
            tool_call.arguments.get("scope") or MemoryScope.PROJECT.value
        ).strip()
        if scope not in {item.value for item in MemoryScope}:
            return "scope must be project or user"

        target_memory_id = str(
            tool_call.arguments.get("target_memory_id") or ""
        ).strip()
        if action == MemoryConsolidationAction.CREATE.value:
            return (
                "CREATE cannot specify target_memory_id"
                if target_memory_id
                else ""
            )
        if not target_memory_id:
            return f"{action} requires target_memory_id"
        target_record = next(
            (
                memory_record
                for memory_record in session.memory_management_candidates
                if memory_record.memory_id == target_memory_id
            ),
            None,
        )
        if target_record is None:
            return "target_memory_id is not in the current-turn management candidates"
        if target_record.scope != scope:
            return "target_memory_id scope does not match requested scope"
        return ""

    def _reject_memory_without_provenance(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        step: int,
    ) -> ToolCallOutcome:
        """找不到当前 user Message 原文时拒绝写入，不创建 operation record。"""

        observation = Observation(
            tool_name=tool_call.name,
            success=False,
            content=(
                "memory_write_rejected: source_quote must exactly match text in a "
                "current-session human-authority role=user message"
            ),
        )
        self.tool_feedback.append_tool_observation(
            session,
            tool_call,
            observation,
            step,
        )
        self._record_memory_authorization_rejection(
            session=session,
            tool_call=tool_call,
            step=step,
        )
        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(
                status=TaskRunStatus.RUNNING,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=observation.content,
                messages_count=len(session.messages),
                observations_count=len(session.observations),
            )
        )
        return ToolCallOutcome(
            status=ToolCallStatus.FAILED,
            reason="memory_user_provenance_not_found",
        )

    def _reject_invalid_memory_consolidation(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        step: int,
        error: str,
    ) -> ToolCallOutcome:
        """在创建 Operation 前把越权或畸形 consolidation 提案变成明确 Observation。"""

        observation = Observation(
            tool_name=tool_call.name,
            success=False,
            content=f"memory_consolidation_rejected: {error}",
        )
        self.tool_feedback.append_tool_observation(
            session,
            tool_call,
            observation,
            step,
        )
        self._record_memory_consolidation_rejection(
            session=session,
            tool_call=tool_call,
            step=step,
            error=error,
        )
        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(
                status=TaskRunStatus.RUNNING,
                current_step=step,
                last_tool=tool_call.name,
                last_observation=observation.content,
                messages_count=len(session.messages),
                observations_count=len(session.observations),
            )
        )
        return ToolCallOutcome(
            status=ToolCallStatus.FAILED,
            reason="memory_consolidation_rejected",
        )

    def _record_memory_consolidation_rejection(
        self,
        *,
        session: AgentRunSession,
        tool_call: ToolCall,
        step: int,
        error: str,
    ) -> None:
        """把 consolidation 拒绝原因集中投影到 Trace。"""

        self.trace.add(
            step,
            session.agent_name,
            "memory_consolidation_validation",
            success=False,
            rejection=error,
            action=str(tool_call.arguments.get("action") or ""),
            target_memory_id=str(
                tool_call.arguments.get("target_memory_id") or ""
            ),
        )

    # 批次整形：限制本 model step 调用数；ask_human 建立同 batch 屏障。
    def _select_calls_for_model_step(
        self,
        session: AgentRunSession,
        response: AgentResponse,
        step: int,
    ) -> list[ToolCall]:
        """普通调用受预算限制；全部显式 remember_memory 使用独立控制通道。

        伪代码：无人工问题 -> 保留全部 memory controls + 按普通 budget 截断
        -> 有人工问题 -> 只保留第一个问题，并把其他调用记为 deferred evidence。

        被预算截断或因 HITL 屏障延后的调用只写入证据，不在当前 Model Step 执行；continuation
        会让模型基于人工回答重新规划，而不是继续消费旧调用列表。
        """

        human_input_calls = [
            call for call in response.tool_calls if call.name == "ask_human"
        ]
        # 没有 HITL 屏障时，显式 remembers 都不消耗 read/edit/test 的普通预算。
        if not human_input_calls:
            memory_calls = [
                call for call in response.tool_calls if call.name == "remember_memory"
            ]
            ordinary_calls = [
                call for call in response.tool_calls if call.name != "remember_memory"
            ]
            selected_candidates = [
                *ordinary_calls[: self.max_tool_calls_per_model_step],
                *memory_calls,
            ]
            selected_tool_calls = [
                call
                for call in response.tool_calls
                if any(call is selected for selected in selected_candidates)
            ]
            dropped_tool_calls = ordinary_calls[
                self.max_tool_calls_per_model_step :
            ]
            # 被预算截断的调用没有执行，只记录为模型曾提出但当前 Model Step 未消费的证据。
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
        # ask_human 建立同 Turn 屏障；其他调用留给模型在取得回答后重新规划。
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
            stop_output="blocked: repeated tool call",
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
        """阶段 2：把 ask_human 转成持久化回答或 waiting_human 暂停。

        伪代码：校验 question/choices -> 幂等查找 durable request
        -> pending/cancelled 返回 StopRequest -> responded 回填 Tool Observation。
        """

        # region 1. 参数校验：坏问题作为 Observation 回填，不建立无效人工请求
        # ask_human 也是模型工具协议的一部分；参数错误应作为失败 Observation 反馈给模型，
        # 不能创建一个无法回答的持久化请求并让整个 Run 永久等待。
        question_arguments = tool_call.arguments or {}
        question_text = question_arguments.get("question")
        choice_values = question_arguments.get("choices", [])
        validation_error = ""
        # 空问题无法被回答，作为普通失败 Observation 返回，不创建 durable request。
        if not isinstance(question_text, str) or not question_text.strip():
            validation_error = "invalid arguments: question must be non-empty str"
        # choices 只接受字符串数组，避免持久化无法稳定渲染的选项。
        elif not isinstance(choice_values, list) or any(
            not isinstance(choice, str) for choice in choice_values
        ):
            validation_error = "invalid arguments: choices must be list"

        # 参数错误留在模型工具协议内反馈，不把整个 Run 锁进人工等待状态。
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
        pending_execution = session.lifecycle.checkpoint.pending_execution
        if pending_execution is None:  # pragma: no cover - batch protocol invariant
            raise RuntimeError("ask_human requires a durable pending ToolCall invocation")
        invocation_id = (
            f"{pending_execution.assistant_item_id}:"
            f"{pending_execution.next_tool_call_index}:{tool_call.id}"
        )
        human_input_resolution = session.lifecycle.request_human_input(
            HumanInputQuestion(
                agent_name=session.agent_name,
                kind="tool_question",
                question=str(question_text),
                choices=tuple(str(choice) for choice in choice_values),
                reason="model requested operator input",
                step=step,
                invocation_id=invocation_id,
            )
        )
        # pending/cancelled 都由 Lifecycle 映射为明确 StopRequest；只有 responded 可继续。
        if human_input_resolution.stop is not None:
            return ToolCallOutcome(
                status=ToolCallStatus.STOPPED,
                reason=human_input_resolution.stop.reason,
                stop_request=human_input_resolution.stop,
            )
        # endregion 2. Durable barrier结束

        # region 3. 回答回填：人工输入变成普通 Tool Observation，协议继续
        # Human answer 自身先作为 authoritative user item 持久化；Tool Observation
        # 只负责闭合原 ask_human 协议，不能代替人类输入 provenance。
        human_answer_content = (
            "Operator answer to the requested question:\n"
            f"Question: {human_input_resolution.request.question}\n"
            f"Answer: {human_input_resolution.request.answer}"
        )
        human_answer_item = self.conversation_threads.append(
            session.thread_id,
            ConversationItemDraft(
                item_id=f"human-input:{human_input_resolution.request.request_id}",
                turn_id=session.turn_id,
                # 新 item 必须由 current Run 写入；稳定 request_id 和 payload 负责
                # crash resume 幂等，原请求 Run 作为 provenance 单独保留在 metadata。
                run_id=self.trace.run_id,
                role="user",
                content=human_answer_content,
                metadata={
                    "human_input_request_id": (
                        human_input_resolution.request.request_id
                    ),
                    "human_input_request_run_id": (
                        human_input_resolution.request.run_id
                    ),
                },
                origin="operator",
                human_authority=True,
            ),
        )
        if human_answer_item.sequence not in session.message_sequences:
            session.messages.append(
                Message(
                    role="user",
                    content=human_answer_content,
                    origin="operator",
                    human_authority=True,
                    item_id=human_answer_item.item_id,
                    turn_id=human_answer_item.turn_id,
                    human_input_request_id=str(
                        human_answer_item.metadata.get("human_input_request_id") or ""
                    ),
                )
            )
            session.message_sequences.append(human_answer_item.sequence)
        session.turn_focus = human_answer_content
        session.turn_focus_item_id = human_answer_item.item_id
        session.memory_management_candidates_key = ""
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
        """阶段 5：执行已获授权工具，再提交操作状态、证据和 checkpoint。

        伪代码：最后 terminal 检查 -> durable state change 写 approved/executing
        -> Gateway -> after_tool -> Ledger 提交结果 -> Evidence/Checkpoint
        -> budget gate -> 下一 Model Step 的 tool message。
        """

        # region 1. 最后控制边界：terminal 可阻止启动，输入类信号留到模型边界
        # 这是 ToolGateway 前最后一个 safe point。这里只消费 pause/cancel；steer 和
        # coordination 都不能插在 ToolCall/Observation 中间，必须留到下一模型边界。
        operator_control = self.run_control_handler.consume_pending_signals(
            session,
            step,
            include_model_input_signals=False,
        )
        # pause/cancel 命中后阻止 ToolGateway 启动，避免新的状态变化发生。
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
        # 自动放行且尚无记录的 durable state change 也要先建立 approved 事实。
        if operation_intent.ledger_tracked and not self.operation_tracker.has_record(
            operation_intent
        ):
            self.operation_tracker.ensure_planned(
                operation_intent,
                step=step,
                status="approved",
            )
        # executing 必须先于 Gateway 落盘，崩溃恢复才能把未提交结果视为 unknown。
        if operation_intent.ledger_tracked:
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
        # Gateway 已返回且 after_tool 已规范化后，才允许提交最终 executed/failed 事实。
        if operation_intent.ledger_tracked:
            # 操作状态表记录工具执行后的最终 Observation，不能在 Gateway 调用前抢先写 executed。
            self.operation_tracker.record_execution_result(
                session,
                tool_call,
                operation_intent,
                tool_observation,
                step,
            )

        # ToolCall 的 canonical Observation 必须先 durable append；只有这一步成功后，
        # batch cursor 才能由调用方推进。崩溃时 Ledger 与稳定 tool item 共同决定
        # “回放既有结果”或“outcome unknown”，绝不盲目重复状态变更。
        self.tool_feedback.append_tool_observation(
            session,
            tool_call,
            tool_observation,
            step,
        )
        # Memory mutation changes management-candidate repository state；下一 model step
        # 必须按最新 human-authority input 重查，不能复用写入前的 key。
        if tool_call.name == "remember_memory" and tool_observation.success:
            session.memory_management_candidates_key = ""

        recorded_evidence = session.evidence.add_observation(tool_observation)
        validation_evidence = self.tool_feedback.build_validation_evidence(
            tool_call.name,
            tool_call.arguments or {},
            tool_observation,
        )
        # 只有明确的测试型证据才能改变 ran_tests，普通成功 Observation 不算验证。
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

        # region 4. Model Step 收口：Observation 已持久化，再决定是否继续调用模型
        budget_stop_signal = session.controller.should_stop(
            step,
            estimated_cost_usd=session.estimated_cost_usd,
        )
        # 预算命中时停止在当前 Observation，不再把它送入下一 Model Step 扩张执行。
        if budget_stop_signal is not None:
            budget_stop_request = StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason=budget_stop_signal.reason,
                stop_output=f"blocked: {budget_stop_signal.reason}",
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

        return ToolCallOutcome(
            status=(
                ToolCallStatus.EXECUTED
                if tool_observation.success
                else ToolCallStatus.FAILED
            ),
            reason=("tool_succeeded" if tool_observation.success else "tool_failed"),
        )
        # endregion 4. Model Step 收口结束

    # region 证据记录器
    def _record_memory_authorization(
        self,
        *,
        session: AgentRunSession,
        step: int,
        operation_key: str,
        provenance: JsonObject,
    ) -> None:
        """记录通过原文匹配的 user-message provenance 与 operation key。"""

        self.trace.add(
            step,
            session.agent_name,
            "memory_authorization",
            operation_key=operation_key,
            provenance=provenance,
        )

    def _record_memory_authorization_rejection(
        self,
        *,
        session: AgentRunSession,
        tool_call: ToolCall,
        step: int,
    ) -> None:
        """记录 fail-closed 的记忆授权拒绝，不把它写成 operation。"""

        self.trace.add(
            step,
            session.agent_name,
            "memory_authorization",
            success=False,
            rejection="user_message_provenance_not_found",
            source_quote=str(tool_call.arguments.get("source_quote") or ""),
        )

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
                "limit": self.max_tool_calls_per_model_step,
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

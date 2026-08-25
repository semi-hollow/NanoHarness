"""把操作员控制与 Runtime coordination 转换为 AgentLoop 安全边界迁移。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from agent_forge.runtime.application.run_lifecycle import StopRequest
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.domain.run_control import (
    RUNTIME_COORDINATION_EVIDENCE_PREFIX,
    RunControlKind,
    RunControlSignal,
    RuntimeCoordinationSignal,
)
from agent_forge.runtime.domain.task import TaskCheckpointUpdate, TaskRunStatus
from agent_forge.runtime.domain.thread import ConversationItemDraft
from agent_forge.runtime.ports import EventSink, RunControlPort


@dataclass(frozen=True, kw_only=True)
class RunControlOutcome:
    """一次安全边界检查产生的停止、steer 与 coordination 事实。"""

    stop: StopRequest | None = None
    steered: bool = False
    coordinated: bool = False

    @property
    def model_input_changed(self) -> bool:
        return self.steered or self.coordinated


class RunControlHandler:
    """只在模型/工具安全边界消费控制与协调信号，不伪装进程级抢占。

    pause/cancel 在每个安全边界都可消费；operator steer 和 Runtime coordination
    只在模型边界进入输入。coordination 即使使用 ``role=user`` 传输，也始终带有
    ``human_authority=false``，不能获得操作员授权语义。
    """

    def __init__(self, control: RunControlPort, trace: EventSink) -> None:
        self.control = control
        self.trace = trace

    # 主要入口：先处理终止信号，再把 steer/coordination 注入下一 Model Step 输入。
    def consume_pending_signals(
        self,
        session: AgentRunSession,
        step: int,
        *,
        include_model_input_signals: bool = True,
        boundary: str = "before_model",
    ) -> RunControlOutcome:
        """消费当前安全边界允许处理的控制与协调输入。

        伪代码：优先消费 pause/cancel -> 若当前不是模型边界则保留输入类信号
        -> 分别 drain operator steer 与 coordination -> 注入不同来源标记
        -> 分开记录 checkpoint/trace provenance -> 返回模型输入是否变化。

        ``include_model_input_signals=False`` 用于工具事务边界：仍允许操作员阻止
        状态变更操作启动，但把 steer 和 coordination 留到下一模型边界。
        """

        # region 1. 终止类信号：pause/cancel 优先，并转换为可持久化 StopRequest
        # RunController 每个 run 只保留一个最新 terminal 信号；消费后立即返回，
        # 保证 pause/cancel 不会与后面的模型输入信号同时改变同一个安全边界。
        terminal_control_signal = self.control.take_terminal(self.trace.run_id)
        # terminal 优先级最高；一旦命中，本边界不再 drain steer 或 coordination。
        if terminal_control_signal is not None:
            self._record_control_signal(session, step, terminal_control_signal)
            terminal_stop_status = (
                TaskRunStatus.CANCELLED
                if terminal_control_signal.kind == RunControlKind.CANCEL
                else TaskRunStatus.PAUSED
            )
            return RunControlOutcome(
                stop=StopRequest(
                    status=terminal_stop_status,
                    reason=terminal_control_signal.kind.value,
                    stop_output=(
                        f"{terminal_control_signal.kind.value}: "
                        f"{terminal_control_signal.reason}"
                    ),
                    current_step=step,
                    messages_count=len(session.messages),
                    observations_count=len(session.observations),
                    resume_hint=(
                        "Resume from this checkpoint to continue; already completed "
                        "side effects are not rolled back."
                    ),
                )
            )
        # endregion 1. 终止类信号结束

        # region 2. 非模型边界：保留输入类信号，避免插入 assistant/tool 协议事务中间
        # ToolCall 与 Observation 必须相邻，因此两类模型输入信号都继续留在队列。
        if not include_model_input_signals:
            return RunControlOutcome()

        pending_steer_signals = self.control.drain_steers(self.trace.run_id)
        coordination_signals = self.control.drain_coordination(
            self.trace.run_id,
            boundary=boundary,
        )
        # 两条来源都为空时不修改 Conversation、Checkpoint 或 Trace。
        if not pending_steer_signals and not coordination_signals:
            return RunControlOutcome()
        # endregion 2. 非模型边界结束

        # region 3. 模型边界：分别注入操作员方向和非人工协调证据
        # steer 以明确的 Operator envelope 进入下一次 llm.chat，并保留人工控制审计。
        for steer_signal in pending_steer_signals:
            steer_content = (
                "Operator steer for the current task:\n"
                + steer_signal.message.strip()
            )
            steer_identity = hashlib.sha256(
                json.dumps(
                    steer_signal.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:20]
            steer_item = session.conversation_threads.append(
                session.thread_id,
                ConversationItemDraft(
                    item_id=f"operator-steer:{session.turn_id}:{steer_identity}",
                    turn_id=session.turn_id,
                    run_id=self.trace.run_id,
                    role="user",
                    content=steer_content,
                    metadata={"signal": steer_signal.to_dict()},
                    origin="operator",
                    human_authority=True,
                ),
            )
            # 幂等 append 已存在时不重复注入当前进程的模型输入视图。
            if steer_item.sequence not in session.message_sequences:
                session.messages.append(
                    Message(
                        role="user",
                        content=steer_content,
                        origin="operator",
                        human_authority=True,
                        item_id=steer_item.item_id,
                        turn_id=steer_item.turn_id,
                    )
                )
                session.message_sequences.append(steer_item.sequence)
            session.turn_focus = steer_content
            session.turn_focus_item_id = steer_item.item_id
            session.memory_management_candidates_key = ""
            self._record_control_signal(session, step, steer_signal)
        # coordination 也使用 user role 作为传输编码，但 envelope 明确否认 human authority。
        for coordination_signal in coordination_signals:
            coordination_content = (
                RUNTIME_COORDINATION_EVIDENCE_PREFIX
                + coordination_signal.content.strip()
            )
            coordination_item = session.conversation_threads.append(
                session.thread_id,
                ConversationItemDraft(
                    item_id=(
                        f"coordination:{session.turn_id}:"
                        f"{coordination_signal.event_id}"
                    ),
                    turn_id=session.turn_id,
                    run_id=self.trace.run_id,
                    role="user",
                    content=coordination_content,
                    metadata={"signal": coordination_signal.to_dict()},
                    origin="runtime_coordination",
                    human_authority=False,
                ),
            )
            # 同一 coordination event 在 resume 时只恢复一份进程内视图。
            if coordination_item.sequence not in session.message_sequences:
                session.messages.append(
                    Message(
                        role="user",
                        content=coordination_content,
                        origin="runtime_coordination",
                        human_authority=False,
                        item_id=coordination_item.item_id,
                        turn_id=coordination_item.turn_id,
                    )
                )
                session.message_sequences.append(coordination_item.sequence)
            self._record_coordination_signal(
                session,
                step,
                coordination_signal,
                boundary=boundary,
            )
        # 两类 provenance 使用不同 metadata key；任何一类都不能覆盖另一类历史。
        metadata = dict(session.lifecycle.checkpoint.metadata)
        stored_steer_messages = metadata.get("steer_messages")
        updated_steer_messages = (
            list(stored_steer_messages)
            if isinstance(stored_steer_messages, list)
            else []
        )
        updated_steer_messages.extend(
            steer_signal.message[:1_000] for steer_signal in pending_steer_signals
        )
        metadata["steer_messages"] = updated_steer_messages[-10:]
        stored_coordination = metadata.get("runtime_coordination")
        updated_coordination = (
            list(stored_coordination)
            if isinstance(stored_coordination, list)
            else []
        )
        updated_coordination.extend(
            coordination_signal.to_dict()
            for coordination_signal in coordination_signals
        )
        metadata["runtime_coordination"] = updated_coordination[-20:]
        session.lifecycle.update_checkpoint(
            TaskCheckpointUpdate(
                current_step=step,
                messages_count=len(session.messages),
                metadata=metadata,
            )
        )
        return RunControlOutcome(
            steered=bool(pending_steer_signals),
            coordinated=bool(coordination_signals),
        )
        # endregion 3. 模型边界结束

    def _record_control_signal(
        self,
        session: AgentRunSession,
        step: int,
        signal: RunControlSignal,
    ) -> None:
        """记录在安全边界实际消费的 pause、cancel 或 steer。"""

        self.trace.add(
            step,
            session.agent_name,
            "run_control",
            control=signal.to_dict(),
        )

    def _record_coordination_signal(
        self,
        session: AgentRunSession,
        step: int,
        signal: RuntimeCoordinationSignal,
        *,
        boundary: str,
    ) -> None:
        """以独立事件类型记录非人工协调身份、版本和实际消费边界。"""

        self.trace.add(
            step,
            session.agent_name,
            "runtime_coordination",
            boundary=boundary,
            coordination=signal.to_dict(),
        )

"""把外部 pause、cancel 和 steer 转换为 AgentLoop 状态迁移。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_forge.runtime.application.run_lifecycle import StopRequest
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.domain.run_control import (
    RunControlKind,
    RunControlSignal,
    RuntimeCoordinationSignal,
)
from agent_forge.runtime.domain.task import TaskCheckpointUpdate, TaskRunStatus
from agent_forge.runtime.ports import EventSink, RunControlPort


@dataclass(frozen=True, kw_only=True)
class RunControlOutcome:
    """一次安全边界检查产生的停止请求和 steer 事实。"""

    stop: StopRequest | None = None
    steered: bool = False
    coordinated: bool = False

    @property
    def model_input_changed(self) -> bool:
        return self.steered or self.coordinated


class RunControlHandler:
    """只在模型/工具安全边界消费控制信号，不伪装进程级抢占。

    pause/cancel 在每个安全边界都可消费；steer 只在模型边界消费。这样不会在一条
    assistant ToolCall 与对应 tool Observation 之间插入 user message，破坏消息协议。
    """

    def __init__(self, control: RunControlPort, trace: EventSink) -> None:
        self.control = control
        self.trace = trace

    # 主要入口：处理终止信号，并按顺序把 steer 注入下一轮会话。
    def consume_pending_signals(
        self,
        session: AgentRunSession,
        step: int,
        *,
        include_steer: bool = True,
        boundary: str = "before_model",
    ) -> RunControlOutcome:
        """返回 pause/cancel 停止请求；steer 只追加新的用户消息。

        ``include_steer=False`` 用于工具前的最后检查：仍允许操作员阻止状态变更操作启动，但把
        改方向消息留在队列，直到下一次模型输入组装前。若 steer 在模型调用期间到达，
        AgentLoop 会丢弃已经过时的模型响应，再开始下一 turn。
        """

        # region 1. 终止类信号：pause/cancel 优先，并转换为可持久化 StopRequest
        # RunController 每个 run 只保留一个最新 terminal 信号；消费后立即返回，
        # 保证 pause/cancel 不会与后面的 steer 同时改变同一个安全边界。
        terminal_control_signal = self.control.take_terminal(self.trace.run_id)
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

        # region 2. 工具边界：保留 steer，避免插入 assistant/tool 协议事务中间
        if not include_steer:
            return RunControlOutcome()

        pending_steer_signals = self.control.drain_steers(self.trace.run_id)
        coordination_signals = self.control.drain_coordination(
            self.trace.run_id,
            boundary=boundary,
        )
        if not pending_steer_signals and not coordination_signals:
            return RunControlOutcome()
        # endregion 2. 工具边界结束

        # region 3. 模型边界：按到达顺序注入 steer，并持久化最近控制历史
        # drain_steers 一次取走当前 FIFO 队列；每条消息以 user role 追加，
        # 使下一次 llm.chat 看见新方向，同时 checkpoint 只保留最近十条审计摘要。
        for steer_signal in pending_steer_signals:
            session.messages.append(
                Message(
                    role="user",
                    content=(
                        "Operator steer for the current task:\n"
                        + steer_signal.message.strip()
                    ),
                )
            )
            self._record_control_signal(session, step, steer_signal)
        for coordination_signal in coordination_signals:
            session.messages.append(
                Message(
                    role="user",
                    content=(
                        "[RUNTIME COORDINATION EVIDENCE]\n"
                        "human_authority=false\n"
                        + coordination_signal.content.strip()
                    ),
                )
            )
            self._record_coordination_signal(
                session,
                step,
                coordination_signal,
                boundary=boundary,
            )
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
        self.trace.add(
            step,
            session.agent_name,
            "runtime_coordination",
            boundary=boundary,
            coordination=signal.to_dict(),
        )

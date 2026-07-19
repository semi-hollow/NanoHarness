"""把外部 pause、cancel 和 steer 转换为 AgentLoop 状态迁移。"""

from __future__ import annotations

from dataclasses import dataclass

from agent_forge.runtime.application.run_lifecycle import StopRequest
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.domain.conversation import Message
from agent_forge.runtime.domain.run_control import RunControlKind
from agent_forge.runtime.domain.task import TaskCheckpointUpdate, TaskRunStatus
from agent_forge.runtime.ports import EventSink, RunControlPort


@dataclass(frozen=True)
class RunControlOutcome:
    """一次安全边界检查产生的停止请求和 steer 事实。"""

    stop: StopRequest | None = None
    steered: bool = False


class ApplyRunControl:
    """只在模型/工具安全边界消费控制信号，不伪装进程级抢占。"""

    def __init__(self, control: RunControlPort, trace: EventSink) -> None:
        self.control = control
        self.trace = trace

    # 主要入口：处理终止信号，并按顺序把 steer 注入下一轮会话。
    def check(
        self,
        session: AgentRunSession,
        step: int,
        *,
        include_steer: bool = True,
    ) -> RunControlOutcome:
        """返回 pause/cancel 停止请求；steer 只追加新的用户消息。"""

        terminal = self.control.take_terminal(self.trace.run_id)
        if terminal is not None:
            self.trace.add(
                step,
                session.agent_name,
                "run_control",
                control=terminal.to_dict(),
            )
            status = (
                TaskRunStatus.CANCELLED
                if terminal.kind == RunControlKind.CANCEL
                else TaskRunStatus.PAUSED
            )
            return RunControlOutcome(
                stop=StopRequest(
                    status=status,
                    reason=terminal.kind.value,
                    final_answer=f"{terminal.kind.value}: {terminal.reason}",
                    current_step=step,
                    messages_count=len(session.messages),
                    observations_count=len(session.observations),
                    resume_hint=(
                        "Resume from this checkpoint to continue; already completed "
                        "side effects are not rolled back."
                    ),
                )
            )
        if not include_steer:
            return RunControlOutcome()

        steers = self.control.drain_steers(self.trace.run_id)
        if not steers:
            return RunControlOutcome()
        for signal in steers:
            session.messages.append(
                Message(
                    "user",
                    "Operator steer for the current task:\n" + signal.message.strip(),
                )
            )
            self.trace.add(
                step,
                session.agent_name,
                "run_control",
                control=signal.to_dict(),
            )
        metadata = dict(session.lifecycle.checkpoint.metadata)
        prior = metadata.get("steer_messages")
        messages = list(prior) if isinstance(prior, list) else []
        messages.extend(signal.message[:1_000] for signal in steers)
        metadata["steer_messages"] = messages[-10:]
        session.lifecycle.update(
            TaskCheckpointUpdate(
                current_step=step,
                messages_count=len(session.messages),
                metadata=metadata,
            )
        )
        return RunControlOutcome(steered=True)

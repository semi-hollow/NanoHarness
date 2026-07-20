"""一次 Agent run 的 checkpoint、人工暂停和停止持久化。"""

from __future__ import annotations

from dataclasses import dataclass, replace

from agent_forge.contracts import JsonObject
from agent_forge.runtime.domain.human_input import (
    HumanInputQuestion,
    HumanInputRequest,
    HumanInputRequestDraft,
)
from agent_forge.runtime.domain.governance import HookDecisionType
from agent_forge.runtime.domain.task import (
    TaskCheckpoint,
    TaskCheckpointUpdate,
    TaskRunStatus,
)
from agent_forge.runtime.ports import (
    EventSink,
    HookPort,
    HumanInputRepository,
    TaskStateRepository,
)


@dataclass(frozen=True)
class StopRequest:
    """让 ``AgentLoop`` 停止所需的完整、可持久化信息。"""

    status: TaskRunStatus
    reason: str
    final_answer: str
    current_step: int | None = None
    last_tool: str | None = None
    last_observation: str | None = None
    resume_hint: str | None = None
    messages_count: int | None = None
    observations_count: int | None = None
    metadata: JsonObject | None = None


@dataclass(frozen=True)
class HumanInputResolution:
    """一次人工问题的持久化结果，以及是否需要暂停运行。"""

    request: HumanInputRequest
    stop: StopRequest | None = None


@dataclass
class RunLifecycle:
    """统一管理 checkpoint、人工暂停和最终停止。

    这是状态持久化边界，不负责模型或工具策略。``AgentLoop`` 和
    ``ToolExecutionPipeline`` 都通过它更新同一份 checkpoint，避免各自拼装字段。
    """

    checkpoint: TaskCheckpoint
    task_state_store: TaskStateRepository
    human_input_store: HumanInputRepository
    human_thread_id: str
    workspace: str
    trace: EventSink
    hooks: HookPort

    # 第一遍：三个 public port 分别对应更新、停止和人工暂停。
    # 运行时端口：同步更新内存 checkpoint、持久化状态和 trace 事实。
    def update(self, update: TaskCheckpointUpdate) -> TaskCheckpoint:
        """持久化一次显式状态转换，并发布同一 checkpoint 的审计事实。

        流程位置：所有非终态 lifecycle transition 的唯一写入点。
        规范上游：Runtime application services。
        下一 owner：``TaskStateRepository``、EventSink、checkpoint hook。
        状态与证据：同一 ``TaskCheckpoint`` 同时进入 durable state 与 trace。
        系统不变量：外围 Adapter 不得绕过本方法直接修改状态字符串。
        """

        self.checkpoint = self.task_state_store.update(
            self.checkpoint,
            update,
        )
        self.trace.record_task_state_checkpoint(
            step=self.checkpoint.current_step,
            agent_name=self.checkpoint.agent_name,
            checkpoint=self.checkpoint,
        )
        self.hooks.on_checkpoint(self.checkpoint)
        return self.checkpoint

    # 运行时端口：统一落盘终态、停止原因和最终文本。
    def stop(self, request: StopRequest) -> str:
        """把黄金主链的所有退出分支归一化为唯一 terminal transition。

        流程位置：黄金主链唯一 terminal transition。
        规范上游：``AgentLoop._stop``。
        下一 owner：stop hook、``TaskStateRepository``、EventSink。
        状态与证据：effective status、stop reason、final text 写入 checkpoint/trace。
        系统不变量：质量门可降级完成状态；外围不能绕过这里宣称完成。
        删除/内联影响：会产生多个 terminal-state owner，并破坏 checkpoint/trace 一致性。
        """

        hook_decisions = self.hooks.on_stop(
            self.trace.run_id,
            request.reason,
            request.final_answer,
        )
        denied = next(
            (
                decision
                for decision in hook_decisions
                if decision.decision in {HookDecisionType.DENY, HookDecisionType.ASK}
            ),
            None,
        )
        effective = request
        if denied is not None and request.status == TaskRunStatus.COMPLETED:
            effective = replace(
                request,
                status=TaskRunStatus.BLOCKED,
                reason="stop_hook_blocked",
                final_answer=f"blocked by {denied.hook_name}: {denied.reason}",
                resume_hint="Satisfy the stop quality gate before claiming completion.",
            )
        self.trace.set_run_context(
            stop_reason=effective.reason,
            final_answer=effective.final_answer,
        )
        self.update(
            TaskCheckpointUpdate(
                status=effective.status,
                stop_reason=effective.reason,
                final_answer=effective.final_answer,
                current_step=effective.current_step,
                last_tool=effective.last_tool,
                last_observation=effective.last_observation,
                resume_hint=effective.resume_hint,
                messages_count=effective.messages_count,
                observations_count=effective.observations_count,
                metadata=effective.metadata,
            )
        )
        self.trace.add(
            effective.current_step or 0,
            self.checkpoint.agent_name,
            "stop_hooks",
            hook_decisions=[decision.to_dict() for decision in hook_decisions],
            stop_reason=effective.reason,
        )
        self.trace.add(
            effective.current_step or 0,
            self.checkpoint.agent_name,
            "run_completed",
            run_status=effective.status.value,
            stop_reason=effective.reason,
        )
        return effective.final_answer

    # 运行时端口：先持久化人工问题和 checkpoint，再返回 waiting_human。
    def request_human_input(
        self,
        question: HumanInputQuestion,
    ) -> HumanInputResolution:
        """解析人工问题的 durable 状态，并返回回答或可恢复的暂停请求。

        流程位置：运行前澄清或工具 HITL 的 durable barrier。
        规范上游：clarification 或 tool governance。
        下一 owner：``HumanInputRepository``；等待时回到 ``AgentLoop``/``stop``。
        状态与证据：human request、trace、WAITING_HUMAN checkpoint。
        系统不变量：request 必须先持久化，进程退出后仍能定位同一问题。
        """

        request = self.human_input_store.request(
            HumanInputRequestDraft(
                thread_id=self.human_thread_id,
                kind=question.kind,
                question=question.question,
                choices=question.choices,
                workspace=self.workspace,
                run_id=self.trace.run_id,
                step=question.step,
                agent_name=question.agent_name,
                reason=question.reason,
            )
        )
        if request.status == "responded":
            return HumanInputResolution(request)

        last_tool = "ask_human" if question.kind == "tool_question" else ""
        if request.status == "cancelled":
            self.trace.add(
                question.step,
                question.agent_name,
                "human_input_cancelled",
                request=request.to_dict(),
            )
            return HumanInputResolution(
                request,
                StopRequest(
                    status=TaskRunStatus.BLOCKED,
                    reason="human_input_cancelled",
                    final_answer=(
                        f"blocked: human_input_cancelled request_id={request.request_id}"
                    ),
                    current_step=question.step,
                    last_tool=last_tool,
                    resume_hint=(
                        "Start a new human-input thread if the task should be reconsidered."
                    ),
                ),
            )

        metadata = dict(self.checkpoint.metadata or {})
        metadata.update(
            {
                "human_thread_id": self.human_thread_id,
                "human_input_request_id": request.request_id,
            }
        )
        self.trace.add(
            question.step,
            question.agent_name,
            "human_input_requested",
            request=request.to_dict(),
        )
        return HumanInputResolution(
            request,
            StopRequest(
                status=TaskRunStatus.WAITING_HUMAN,
                reason="waiting_human",
                final_answer=(
                    f"waiting_human: {request.question} "
                    f"request_id={request.request_id} request={request.path}"
                ),
                current_step=question.step,
                last_tool=last_tool,
                resume_hint=(
                    f"Run `forge respond {request.request_id} --answer <text>` then resume this run."
                ),
                metadata=metadata,
            ),
        )

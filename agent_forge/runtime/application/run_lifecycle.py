"""一次 Agent run 的 checkpoint、人工暂停和停止持久化。"""

from __future__ import annotations

from dataclasses import dataclass, replace

from agent_forge.contracts import JsonObject
from agent_forge.observability.domain.event import TraceEventType
from agent_forge.runtime.domain.human_input import (
    HumanInputQuestion,
    HumanInputRequest,
    HumanInputRequestDraft,
)
from agent_forge.runtime.domain.governance import HookDecision, HookDecisionType
from agent_forge.runtime.domain.task import (
    TaskCheckpoint,
    TaskCheckpointUpdate,
    TaskRunStatus,
)
from agent_forge.runtime.domain.thread import ConversationItemDraft
from agent_forge.runtime.ports import (
    EventSink,
    HookPort,
    HumanInputRepository,
    TaskStateRepository,
)
from agent_forge.runtime.ports.thread import ConversationThreadRepository


@dataclass(frozen=True, kw_only=True)
class StopRequest:
    """让 ``AgentLoop`` 停止所需的完整、可持久化信息。"""

    status: TaskRunStatus
    reason: str
    stop_output: str
    candidate_final_answer: str | None = None
    current_step: int | None = None
    last_tool: str | None = None
    last_observation: str | None = None
    resume_hint: str | None = None
    messages_count: int | None = None
    observations_count: int | None = None
    metadata: JsonObject | None = None


@dataclass(frozen=True, kw_only=True)
class HumanInputResolution:
    """一次人工问题的持久化结果，以及是否需要暂停运行。"""

    request: HumanInputRequest
    stop: StopRequest | None = None


@dataclass(kw_only=True)
class RunLifecycle:
    """统一管理 checkpoint、人工暂停和最终停止。

    这是状态持久化边界，不负责模型或工具策略。``AgentLoop`` 和
    ``ToolExecutionPipeline`` 都通过它更新同一份 checkpoint，避免各自拼装字段。
    """

    checkpoint: TaskCheckpoint
    task_state_store: TaskStateRepository
    conversation_threads: ConversationThreadRepository
    thread_id: str
    turn_id: str
    human_input_store: HumanInputRepository
    workspace: str
    trace: EventSink
    hooks: HookPort

    # 三个公开入口分别对应更新、停止和人工暂停。
    # 运行时端口：同步更新内存 checkpoint、持久化状态和 trace 事实。
    def update_checkpoint(
        self,
        update: TaskCheckpointUpdate,
    ) -> TaskCheckpoint:
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

    # 运行时端口：统一落盘终态、停止原因和调用方停止输出。
    def finalize_run(self, requested_stop: StopRequest) -> str:
        """把黄金主链的所有退出分支归一化为唯一 terminal transition。

        流程位置：黄金主链唯一 terminal transition。
        规范上游：``AgentLoop._finalize_run``。
        下一 owner：stop hook、``TaskStateRepository``、EventSink。
        状态与证据：最终 status、stop reason、final text 写入 checkpoint/trace。
        系统不变量：质量门可降级完成状态；外围不能绕过这里宣称完成。
        删除/内联影响：会产生多个 terminal-state owner，并破坏 checkpoint/trace 一致性。
        """

        # region 1. 停止质量门：模型只能提出完成，Hook 可以在落盘前否决
        # on_stop 读取候选回答；_apply_completion_quality_gate 把 Hook 结论收敛为
        # 最终 StopRequest，只有仍为 COMPLETED 的候选才成为 accepted final answer。
        durable_final_before_stop = self.existing_accepted_final_answer()
        recovering_accepted_final = (
            durable_final_before_stop is not None
            and requested_stop.status == TaskRunStatus.COMPLETED
            and requested_stop.candidate_final_answer == durable_final_before_stop
        )
        # accepted final 已证明上次 attempt 通过 stop hooks 并先于
        # checkpoint 落盘；crash resume 只补终态，不重放可能有外部行为的 hook。
        if recovering_accepted_final:
            hook_decisions: list[HookDecision] = []
            final_stop_request = requested_stop
        else:
            hook_input = (
                requested_stop.candidate_final_answer or requested_stop.stop_output
            )
            hook_decisions = self.hooks.on_stop(
                self.trace.run_id,
                requested_stop.reason,
                hook_input,
            )
            final_stop_request = self._apply_completion_quality_gate(
                requested_stop=requested_stop,
                hook_decisions=hook_decisions,
            )
        accepted_final_answer = (
            final_stop_request.candidate_final_answer
            if final_stop_request.status == TaskRunStatus.COMPLETED
            else None
        )
        if accepted_final_answer is not None:
            existing_final = durable_final_before_stop
            if existing_final is not None and existing_final != accepted_final_answer:
                raise RuntimeError(
                    "accepted final answer conflicts with durable ConversationThread"
                )
            if existing_final is None:
                self.conversation_threads.append(
                    self.thread_id,
                    ConversationItemDraft(
                        item_id=f"final:{self.trace.run_id}",
                        turn_id=self.turn_id,
                        run_id=self.trace.run_id,
                        role="assistant",
                        content=accepted_final_answer,
                        origin="model_final",
                        human_authority=False,
                    ),
                )
        elif final_stop_request.candidate_final_answer is not None:
            # 被 output/stop governance 拒绝的文本仍是已发生的模型事实，但不能使用
            # model_final 身份，也不能被外围渲染成 accepted final answer。
            self.conversation_threads.append(
                self.thread_id,
                ConversationItemDraft(
                    item_id=f"final-candidate:{self.trace.run_id}",
                    turn_id=self.turn_id,
                    run_id=self.trace.run_id,
                    role="assistant",
                    content=final_stop_request.candidate_final_answer,
                    metadata={
                        "accepted": False,
                        "rejection": final_stop_request.reason,
                    },
                    origin="model_final_candidate",
                    human_authority=False,
                ),
            )
        # endregion 1. 停止质量门结束

        # region 2. Durable state：把最终决定写入 checkpoint，而非原始请求
        # 质量门可能把 requested COMPLETED 降级为 BLOCKED；Trace 上下文和 checkpoint
        # 必须共同使用 final_stop_request，不能分别记录模型请求和治理后状态。
        self.trace.set_run_context(
            stop_reason=final_stop_request.reason,
            stop_output=final_stop_request.stop_output,
            final_answer=accepted_final_answer,
        )
        is_terminal = final_stop_request.status in {
            TaskRunStatus.CANCELLED,
            TaskRunStatus.BLOCKED,
            TaskRunStatus.FAILED,
            TaskRunStatus.COMPLETED,
        }
        if is_terminal:
            # 先把“哪个 Run 要以什么状态结束 Turn”写入 Thread，再落 terminal
            # checkpoint。若进程恰好死在 checkpoint 与 finish_turn 之间，Thread
            # loader 会校验这两个 durable 事实并幂等补完收口；不会要求模型重跑。
            self.conversation_threads.prepare_turn_terminal(
                self.thread_id,
                self.turn_id,
                run_id=self.checkpoint.run_id,
                status=final_stop_request.status.value,
            )
        self.update_checkpoint(
            TaskCheckpointUpdate(
                status=final_stop_request.status,
                stop_reason=final_stop_request.reason,
                stop_output=final_stop_request.stop_output,
                final_answer=accepted_final_answer,
                current_step=final_stop_request.current_step,
                last_tool=final_stop_request.last_tool,
                last_observation=final_stop_request.last_observation,
                resume_hint=final_stop_request.resume_hint,
                messages_count=final_stop_request.messages_count,
                observations_count=final_stop_request.observations_count,
                metadata=final_stop_request.metadata,
            )
        )
        # endregion 2. Durable state结束

        # region 3. 终态证据：发布质量门与最终状态两个层次的事实
        self._record_terminal_evidence(
            final_stop_request=final_stop_request,
            hook_decisions=hook_decisions,
            final_answer_accepted=accepted_final_answer is not None,
        )
        if is_terminal:
            self.conversation_threads.finish_turn(
                self.thread_id,
                self.turn_id,
                run_id=self.checkpoint.run_id,
                status=final_stop_request.status.value,
            )
        return final_stop_request.stop_output
        # endregion 3. 终态证据结束

    def existing_accepted_final_answer(self) -> str | None:
        """返回当前 Turn 已 durable append 的 accepted final，供 crash resume 收口。"""

        for item in reversed(
            self.conversation_threads.list_recent_items(
                self.thread_id,
                turn_id=self.turn_id,
                limit=50,
            )
        ):
            if item.role == "assistant" and item.origin == "model_final":
                return item.content
        return None

    # 运行时端口：先持久化人工问题和 checkpoint，再返回 waiting_human。
    def request_human_input(
        self,
        question: HumanInputQuestion,
    ) -> HumanInputResolution:
        """解析人工问题的 durable 状态，并返回回答或可恢复的暂停请求。

        流程位置：运行前澄清或工具 HITL 的 durable barrier。
        规范上游：clarification 或 tool governance。
        下一 owner：``HumanInputRepository``；等待时回到
        ``AgentLoop``/``finalize_run``。
        状态与证据：human request、trace、WAITING_HUMAN checkpoint。
        系统不变量：request 必须先持久化，进程退出后仍能定位同一问题。
        """

        # region 1. 请求落盘：先取得稳定 request_id，再决定继续或暂停
        # 相同 canonical invocation 会定位同一 durable 请求；responded 表示恢复路径已带
        # 回答，可以立即返回给调用方；后续独立 ask_human 即使文本相同也使用新身份。
        human_input_request = self.human_input_store.request(
            HumanInputRequestDraft(
                thread_id=self.thread_id,
                turn_id=self.turn_id,
                kind=question.kind,
                question=question.question,
                choices=question.choices,
                workspace=self.workspace,
                run_id=self.trace.run_id,
                step=question.step,
                agent_name=question.agent_name,
                reason=question.reason,
                invocation_id=question.invocation_id,
            )
        )
        if human_input_request.status == "responded":
            return HumanInputResolution(request=human_input_request)
        # endregion 1. 请求落盘结束

        # region 2. 已取消分支：保留审计事实，并把运行转为不可继续的 BLOCKED
        # cancel 表示操作员拒绝继续这个问题，不等同于“尚未回答”；因此不能继续等待，
        # 也不能把空答案注入模型，而要形成明确的 terminal StopRequest。
        last_tool = "ask_human" if question.kind == "tool_question" else ""
        if human_input_request.status == "cancelled":
            self._record_human_input_event(
                event_type="human_input_cancelled",
                question=question,
                human_input_request=human_input_request,
            )
            return HumanInputResolution(
                request=human_input_request,
                stop=StopRequest(
                    status=TaskRunStatus.BLOCKED,
                    reason="human_input_cancelled",
                    stop_output=(
                        "blocked: human_input_cancelled "
                        f"request_id={human_input_request.request_id}"
                    ),
                    current_step=question.step,
                    last_tool=last_tool,
                    resume_hint=(
                        "Start a new human-input thread if the task should be reconsidered."
                    ),
                ),
            )
        # endregion 2. 已取消分支结束

        # region 3. 待回答分支：把恢复定位信息写入 checkpoint metadata
        metadata = dict(self.checkpoint.metadata or {})
        metadata.update(
            {
                "human_input_request_id": human_input_request.request_id,
            }
        )
        self._record_human_input_event(
            event_type="human_input_requested",
            question=question,
            human_input_request=human_input_request,
        )
        return HumanInputResolution(
            request=human_input_request,
            stop=StopRequest(
                status=TaskRunStatus.WAITING_HUMAN,
                reason="waiting_human",
                stop_output=(
                    f"waiting_human: {human_input_request.question} "
                    f"request_id={human_input_request.request_id} "
                    f"request={human_input_request.path}"
                ),
                current_step=question.step,
                last_tool=last_tool,
                resume_hint=(
                    "Run `forge resume <run_dir> --answer <text> "
                    f"--request-id {human_input_request.request_id}`."
                ),
                metadata=metadata,
            ),
        )
        # endregion 3. 待回答分支结束

    # region 终态质量门规则
    @staticmethod
    def _apply_completion_quality_gate(
        *,
        requested_stop: StopRequest,
        hook_decisions: list[HookDecision],
    ) -> StopRequest:
        """只在声明完成时执行质量门，并返回最终应持久化的停止请求。

        ``replace`` 会复制不可变 dataclass，只替换列出的字段；原请求保持不变。
        ASK 与 DENY 都说明完成条件尚未满足，因此统一降级为 BLOCKED。
        """

        if requested_stop.status != TaskRunStatus.COMPLETED:
            return requested_stop

        blocking_hook_decision = RunLifecycle._find_completion_blocking_decision(
            hook_decisions
        )
        if blocking_hook_decision is None:
            return requested_stop

        return replace(
            requested_stop,
            status=TaskRunStatus.BLOCKED,
            reason="stop_hook_blocked",
            stop_output=(
                f"blocked by {blocking_hook_decision.hook_name}: "
                f"{blocking_hook_decision.reason}"
            ),
            resume_hint="Satisfy the stop quality gate before claiming completion.",
        )

    @staticmethod
    def _find_completion_blocking_decision(
        hook_decisions: list[HookDecision],
    ) -> HookDecision | None:
        """按 Hook 顺序返回第一个拒绝或要求人工介入的完成决定。"""

        for hook_decision in hook_decisions:
            completion_is_blocked = hook_decision.decision in {
                HookDecisionType.DENY,
                HookDecisionType.ASK,
            }
            if completion_is_blocked:
                return hook_decision
        return None

    # endregion 终态质量门规则结束

    # region 证据记录器

    def _record_terminal_evidence(
        self,
        *,
        final_stop_request: StopRequest,
        hook_decisions: list[HookDecision],
        final_answer_accepted: bool,
    ) -> None:
        """记录“质量门决定”和“最终状态”两个不同层次的终止事实。"""

        event_step = final_stop_request.current_step or 0
        self.trace.add(
            event_step,
            self.checkpoint.agent_name,
            "stop_hooks",
            hook_decisions=[decision.to_dict() for decision in hook_decisions],
            stop_reason=final_stop_request.reason,
        )
        self.trace.add(
            event_step,
            self.checkpoint.agent_name,
            "run_completed",
            run_status=final_stop_request.status.value,
            stop_reason=final_stop_request.reason,
            final_answer_accepted=final_answer_accepted,
        )

    def _record_human_input_event(
        self,
        *,
        event_type: TraceEventType,
        question: HumanInputQuestion,
        human_input_request: HumanInputRequest,
    ) -> None:
        """记录人工请求进入 pending 或 cancelled 的持久化事实。"""

        self.trace.add(
            question.step,
            question.agent_name,
            event_type,
            request=human_input_request.to_dict(),
        )

    # endregion 证据记录器结束

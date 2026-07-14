"""Single Agent 的主控制循环。

首次阅读只展开 ``AgentLoop.run``。运行前决策、turn 输入、工具治理和最终答案均由
同目录中具名应用服务负责，本类只保留真正的控制流。
"""

from __future__ import annotations

from agent_forge.runtime.application.dependencies import RuntimeDependencies
from agent_forge.runtime.application.final_answer import FinalAnswerBuilder
from agent_forge.runtime.application.run_lifecycle import StopRequest
from agent_forge.runtime.application.run_preparation import RunPreparation
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.application.tool_execution import ToolExecutionPipeline
from agent_forge.runtime.application.turn_preparation import PreparedTurn, TurnPreparation
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.domain.conversation import AgentResponse
from agent_forge.runtime.domain.task import TaskRunStatus


class AgentLoop:
    """单 Agent 控制流的应用服务。

    完整链路：``run`` -> ``_run_turn`` -> model -> final answer/tool pipeline。
    第一遍无需展开其余应用服务；需要定位策略时，再按类名进入对应文件。
    """

    def __init__(
        self,
        config: RuntimeConfig,
        dependencies: RuntimeDependencies,
    ) -> None:
        """接收 composition root 装配的端口，不创建基础设施对象。"""

        self.config = config
        self.trace = dependencies.events
        self.llm = dependencies.model
        human_thread_id = getattr(config, "human_thread_id", "") or self.trace.run_id
        self.run_preparation = RunPreparation(
            config,
            dependencies,
            human_thread_id=human_thread_id,
        )
        self.turn_preparation = TurnPreparation(
            config,
            dependencies.events,
            dependencies.context,
            dependencies.tools,
            dependencies.environment,
        )
        self.tool_execution = ToolExecutionPipeline(
            config,
            dependencies.events,
            dependencies.tools,
            dependencies.hooks,
            dependencies.approvals,
            dependencies.operations,
        )
        self.final_answer = FinalAnswerBuilder(dependencies.events)

    # 主要入口：下方定义承接该模块的核心调用。
    def run(self, task: str, agent_name: str = "CodingAgent") -> str:
        """运行四个阶段：start -> prepare -> turn loop -> stop。

        CLI、顺序多角色、fanout worker 和 benchmark 都调用这里。返回值只是最终
        文本；trace、checkpoint 与 operation ledger 保存可审计证据。
        """

        session = self.run_preparation.start(task, agent_name)
        stop = self.run_preparation.execute(session)
        if stop is not None:
            return self._stop(session, stop)

        for step in range(1, session.max_iterations + 1):
            stop = self._run_turn(session, step)
            if stop is not None:
                return self._stop(session, stop)

        return self._stop(
            session,
            StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason="max_steps",
                final_answer="blocked: max_steps reached",
            ),
        )

    def _run_turn(
        self,
        session: AgentRunSession,
        step: int,
    ) -> StopRequest | None:
        """执行一个 turn：准备输入 -> 调用模型 -> final 或工具分支。"""

        session.iteration = step
        turn = self.turn_preparation.execute(session, step)
        response = self.llm.chat(turn.messages_for_llm, turn.schemas)

        if response.error:
            return self._handle_model_failure(session, response, step)

        self._record_llm_call(session, turn, response)
        usage = getattr(self.llm, "last_usage", None)
        session.estimated_cost_usd = float(
            getattr(usage, "estimated_cost_usd", 0.0) or 0.0
        )
        if not response.tool_calls:
            return self.final_answer.execute(session, response, step)
        return self.tool_execution.execute_calls(
            session,
            response,
            step=step,
            allowed_tool_names=turn.allowed_tool_names,
        )

    def _handle_model_failure(
        self,
        session: AgentRunSession,
        response: AgentResponse,
        step: int,
    ) -> StopRequest:
        """把 provider 失败转换为显式、可恢复的停止请求。"""

        error = response.error or {"code": "unknown_error"}
        signal = session.controller.model_failure(error)
        self.trace.add(
            step,
            session.agent_name,
            "error",
            success=False,
            error=str(error),
        )
        self.trace.add(
            step,
            session.agent_name,
            "recovery_decision",
            success=signal.retryable,
            failure_kind=signal.kind.value,
            retryable=signal.retryable,
            recovery_hint=signal.recovery_hint,
        )
        return StopRequest(
            status=TaskRunStatus.FAILED,
            reason="invalid_llm_response",
            final_answer=f"blocked: invalid llm response: {error}",
            current_step=step,
            resume_hint=signal.recovery_hint,
        )

    def _record_llm_call(
        self,
        session: AgentRunSession,
        turn: PreparedTurn,
        response: AgentResponse,
    ) -> None:
        """记录模型边界的输入规模、输出摘要和 provider usage。"""

        usage = getattr(self.llm, "last_usage", None)
        self.trace.add(
            turn.step,
            session.agent_name,
            "llm_call",
            llm_request_summary=(
                f"messages={len(turn.messages_for_llm)} "
                f"tools={len(turn.schemas)} "
                f"context_chars={len(turn.context_message.content)}"
            ),
            llm_response_summary=response.content or "tool_calls",
            llm_input_breakdown_chars={
                "system_context": len(turn.context_message.content),
                "conversation_history": turn.history_chars,
                "tool_schemas": turn.tool_schema_chars,
            },
            model_usage=usage.to_dict() if usage is not None else {},
        )

    @staticmethod
    def _stop(session: AgentRunSession, request: StopRequest) -> str:
        """更新内存状态，并把唯一 terminal transition 交给 lifecycle。"""

        session.status = (
            "completed"
            if request.status == TaskRunStatus.COMPLETED
            else "failed"
            if request.status == TaskRunStatus.FAILED
            else "stopped"
        )
        session.stop_reason = request.reason
        session.final_answer = request.final_answer
        return session.lifecycle.stop(request)

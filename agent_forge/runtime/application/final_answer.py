"""Single Agent 最终答案的证据拼接与输出校验。"""

from __future__ import annotations

from agent_forge.runtime.application.run_lifecycle import StopRequest
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.domain.conversation import AgentResponse
from agent_forge.runtime.domain.task import TaskRunStatus
from agent_forge.runtime.ports import EventSink
from agent_forge.safety.guardrails import output_guardrail


class FinalAnswerBuilder:
    """把无工具调用的模型响应转换为可停止的最终答案。"""

    def __init__(self, trace: EventSink) -> None:
        self.trace = trace

    # 主要入口：把无 tool call 的模型响应归一化为完成、阻塞或继续验证。
    def execute(
        self,
        session: AgentRunSession,
        response: AgentResponse,
        step: int,
    ) -> StopRequest:
        """把无 ToolCall 的模型响应转换为可由 lifecycle 持久化的停止请求。

        流程位置：模型文本与 terminal transition 之间的 claim boundary。
        规范上游：``AgentLoop``。
        下一 owner：``RunLifecycle.stop``。
        状态与证据：final-answer、citation 与 unverified-claim 事件。
        系统不变量：Harness 完成不等于 local 或 official resolved。
        删除/内联影响：会让模型文本绕过 claim boundary 直接成为完成结论。
        """

        if self._contains_raw_tool_call_markup(response.content or ""):
            final_answer = "blocked: pending_tool_call_at_stop"
            self.trace.add(
                step,
                session.agent_name,
                "final_answer",
                success=False,
                observation=final_answer,
                pending_tool_call=True,
            )
            return StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason="pending_tool_call_at_stop",
                final_answer=final_answer,
                current_step=step,
                messages_count=len(session.messages),
                observations_count=len(session.observations),
                resume_hint=(
                    "Increase step budget or keep required tools routed until the pending call executes."
                ),
            )

        citations = session.evidence.final_citations()
        evidence_text = ""
        if citations:
            evidence_text = "\n证据:\n" + "\n".join(f"- {item}" for item in citations)
        final_answer = (
            (response.content or "")
            + evidence_text
            + "\n未验证点: 未进行真实线上压测。"
        )
        output_check = output_guardrail(
            final_answer,
            session.ran_tests,
            session.blocked,
        )
        self.trace.add(
            step,
            session.agent_name,
            "guardrail_check",
            guardrail={
                "category": output_check.category,
                "passed": output_check.passed,
                "reason": output_check.reason,
                "severity": output_check.severity,
            },
        )
        self.trace.add(
            step,
            session.agent_name,
            "final_answer",
            observation=final_answer,
            evidence_refs=citations,
        )
        return StopRequest(
            status=TaskRunStatus.COMPLETED,
            reason="final_answer",
            final_answer=final_answer,
            current_step=step,
            messages_count=len(session.messages),
            observations_count=len(session.observations),
        )

    @staticmethod
    def _contains_raw_tool_call_markup(content: str) -> bool:
        lowered = content.lower()
        return "tool_calls" in lowered and "invoke name=" in lowered

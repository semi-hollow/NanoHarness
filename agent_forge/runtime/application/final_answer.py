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

    # PRIMARY ENTRYPOINT: validate and package one terminal model response.
    def execute(
        self,
        session: AgentRunSession,
        response: AgentResponse,
        step: int,
    ) -> StopRequest:
        """拒绝未执行的工具标记，并附加可引用证据和未验证声明。"""

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

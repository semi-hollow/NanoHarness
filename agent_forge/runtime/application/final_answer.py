"""Single Agent 最终答案的证据拼接与输出声明检查。"""

from __future__ import annotations

import re

from agent_forge.runtime.application.run_lifecycle import StopRequest
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.domain.conversation import AgentResponse
from agent_forge.runtime.domain.task import TaskRunStatus
from agent_forge.runtime.ports import EventSink
from agent_forge.safety.guardrails import GuardrailResult, output_guardrail


class FinalAnswerBuilder:
    """把无工具调用的模型响应转换为受证据与输出声明约束的停止请求。"""

    def __init__(self, trace: EventSink) -> None:
        self.trace = trace

    # 主要入口：把最终模型响应归一化为完成或阻塞，拒绝任何待执行 ToolCall。
    def build_stop_request(
        self,
        session: AgentRunSession,
        response: AgentResponse,
        step: int,
    ) -> StopRequest:
        """把最终模型响应转换为可由 lifecycle 持久化的停止请求。

        流程位置：模型文本与 terminal transition 之间的 claim boundary。
        规范上游：``AgentLoop``。
        下一 owner：``RunLifecycle.finalize_run``。
        状态与证据：final-answer、citation 与 unverified-claim 事件。
        系统不变量：Harness 完成不等于 local 或 official resolved。
        删除/内联影响：会让模型文本绕过 claim boundary 直接成为完成结论。
        """

        rejected_tool_name = (
            response.tool_calls[0].name
            if response.tool_calls
            else self._raw_tool_name(response.content or "")
        )
        if rejected_tool_name is not None:
            stop_output = "blocked: pending_tool_call_at_stop"
            self._record_rejected_tool_request(
                session=session,
                step=step,
                stop_output=stop_output,
                rejected_tool_name=rejected_tool_name,
            )
            return StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason="pending_tool_call_at_stop",
                stop_output=stop_output,
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
        final_answer = (response.content or "") + evidence_text
        # Output Guardrail 只对极窄的确定性声明做检查；它不是通用
        # 语义 classifier。但 high-severity 且无运行证据的验证结论不能
        # 被 accepted COMPLETED，必须保留为 candidate evidence 并显式阻断。
        output_check = output_guardrail(
            final_answer,
            session.ran_tests,
            session.blocked,
        )
        self._record_final_answer_evidence(
            session=session,
            step=step,
            final_answer=final_answer,
            citations=citations,
            output_check=output_check,
        )
        if not output_check.passed and output_check.severity == "high":
            return StopRequest(
                status=TaskRunStatus.BLOCKED,
                reason="unsupported_validation_claim",
                stop_output=(
                    "blocked: unsupported validation claim; run a governed validation "
                    "tool or describe the result as unverified"
                ),
                candidate_final_answer=final_answer,
                current_step=step,
                messages_count=len(session.messages),
                observations_count=len(session.observations),
                resume_hint=(
                    "Run python_validation/run_command, or remove the unsupported "
                    "test-pass claim."
                ),
            )
        return StopRequest(
            status=TaskRunStatus.COMPLETED,
            reason="final_answer",
            stop_output=final_answer,
            candidate_final_answer=final_answer,
            current_step=step,
            messages_count=len(session.messages),
            observations_count=len(session.observations),
        )

    # region 证据记录器
    def _record_rejected_tool_request(
        self,
        *,
        session: AgentRunSession,
        step: int,
        stop_output: str,
        rejected_tool_name: str,
    ) -> None:
        """记录最终轮仍出现 ToolCall，且该调用没有进入执行链。"""

        self.trace.add(
            step,
            session.agent_name,
            "pending_tool_call_rejected",
            success=False,
            observation=stop_output,
            tool_call=rejected_tool_name,
            pending_tool_call=True,
            rejection_reason="final_turn_tools_closed",
        )

    def _record_final_answer_evidence(
        self,
        *,
        session: AgentRunSession,
        step: int,
        final_answer: str,
        citations: list[str],
        output_check: GuardrailResult,
    ) -> None:
        """分别记录输出语义检查结论和最终文本引用。"""

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
            "candidate_final_answer",
            observation=final_answer,
            evidence_refs=citations,
        )

    # endregion 证据记录器结束

    @staticmethod
    def _raw_tool_name(content: str) -> str | None:
        """从错误落入文本通道的 ToolCall 标记中提取工具名。"""

        normalized_answer = content.lower()
        if (
            "tool_calls" not in normalized_answer
            or "invoke name=" not in normalized_answer
        ):
            return None
        match = re.search(r'invoke\s+name=["\']([^"\']+)["\']', content, re.IGNORECASE)
        return match.group(1) if match else "unknown_tool"

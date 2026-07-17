"""工具结果如何进入模型上下文、恢复策略与 validation evidence。"""

from __future__ import annotations

import json
import shlex
from typing import Any

from agent_forge.contracts import JsonObject, ToolArguments
from agent_forge.runtime.application.session import AgentRunSession
from agent_forge.runtime.control import FailureSignal
from agent_forge.runtime.domain.conversation import Message, Observation, ToolCall
from agent_forge.runtime.ports import EventSink


class ToolFeedback:
    """集中处理工具反馈，避免各治理分支拼装不同消息格式。"""

    def __init__(self, trace: EventSink) -> None:
        self.trace = trace

    def append(
        self,
        session: AgentRunSession,
        tool_call: ToolCall,
        observation: Observation,
        step: int,
    ) -> None:
        """把未执行、被拒绝或人工回答反馈给下一轮模型。"""

        session.working_memory.add_observation(observation)
        session.messages.append(
            Message(
                "tool",
                observation.content,
                name=tool_call.name,
                tool_call_id=tool_call.id,
            )
        )
        self.trace.add(
            step,
            session.agent_name,
            "tool_observation",
            success=observation.success,
            observation=observation.content,
        )

    def record_recovery(
        self,
        session: AgentRunSession,
        observation: Observation,
        step: int,
        *,
        remember: bool = False,
    ) -> FailureSignal | None:
        """分类失败并把恢复建议写入 trace。"""

        signal = session.controller.classify_observation(observation)
        if signal is None:
            return None
        if remember:
            session.working_memory.add(
                f"recovery:{signal.kind.value}:{signal.recovery_hint}"
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
        return signal

    @staticmethod
    def validation_evidence(
        tool_name: str,
        arguments: ToolArguments,
        observation: Observation,
    ) -> JsonObject | None:
        """只把明确的测试命令结果视为 correctness validation。"""

        kind = ""
        if (
            tool_name == "diagnostics"
            and str(arguments.get("kind") or "").lower() == "unittest"
        ):
            kind = "unittest"
        elif tool_name == "run_command":
            try:
                parts = shlex.split(str(arguments.get("command") or ""))
            except ValueError:
                parts = []
            if parts and parts[0].lower() == "pytest":
                kind = "pytest"
            elif len(parts) >= 3 and parts[1:3] in [
                ["-m", "pytest"],
                ["-m", "unittest"],
            ]:
                kind = parts[2].lower()
        if not kind:
            return None

        lowered = observation.content.lower()
        unavailable = any(
            marker in lowered
            for marker in [
                "validation_blocked",
                "missing dependency",
                "no module named",
            ]
        )
        status = (
            "unavailable"
            if unavailable
            else "passed"
            if observation.success
            else "failed"
        )
        return {
            "kind": kind,
            "status": status,
            "tool": tool_name,
            "evidence": observation.content[:600],
        }

    @staticmethod
    def message_tool_call(call: ToolCall) -> dict[str, Any]:
        """转换为 OpenAI-compatible assistant message 结构。"""

        return {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": json.dumps(call.arguments, ensure_ascii=False),
            },
        }

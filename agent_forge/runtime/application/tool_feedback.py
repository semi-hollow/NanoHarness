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

    def append_tool_observation(
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
        self._record_tool_observation(
            session=session,
            observation=observation,
            step=step,
        )

    def record_recovery_decision(
        self,
        session: AgentRunSession,
        observation: Observation,
        step: int,
        *,
        remember: bool = False,
    ) -> FailureSignal | None:
        """分类失败并把恢复建议写入 trace。"""

        recovery_signal = session.controller.classify_observation(observation)
        if recovery_signal is None:
            return None
        if remember:
            session.working_memory.add(
                f"recovery:{recovery_signal.kind.value}:{recovery_signal.recovery_hint}"
            )
        self._record_recovery_evidence(
            session=session,
            recovery_signal=recovery_signal,
            step=step,
        )
        return recovery_signal

    # region 证据记录器（首次阅读可折叠）
    def _record_tool_observation(
        self,
        *,
        session: AgentRunSession,
        observation: Observation,
        step: int,
    ) -> None:
        """记录返回给模型的拒绝、人工回答或其他工具反馈。"""

        self.trace.add(
            step,
            session.agent_name,
            "tool_observation",
            success=observation.success,
            observation=observation.content,
        )

    def _record_recovery_evidence(
        self,
        *,
        session: AgentRunSession,
        recovery_signal: FailureSignal,
        step: int,
    ) -> None:
        """记录控制器对失败类型、可重试性和恢复方向的判断。"""

        self.trace.add(
            step,
            session.agent_name,
            "recovery_decision",
            success=recovery_signal.retryable,
            failure_kind=recovery_signal.kind.value,
            retryable=recovery_signal.retryable,
            recovery_hint=recovery_signal.recovery_hint,
        )

    # endregion 证据记录器结束

    @staticmethod
    def build_validation_evidence(
        tool_name: str,
        arguments: ToolArguments,
        observation: Observation,
    ) -> JsonObject | None:
        """只把明确的测试命令结果视为 correctness validation。"""

        validation_kind = ""
        if tool_name == "python_validation":
            check_type = str(arguments.get("check_type") or "").strip().lower()
            command_marker = f"validation_command=python -m {check_type}"
            command_attested = any(
                line == command_marker or line.startswith(f"{command_marker} ")
                for line in observation.content.lower().splitlines()
            )
            if check_type in {"pytest", "unittest"} and command_attested:
                validation_kind = check_type
        elif tool_name == "run_command":
            try:
                parts = shlex.split(str(arguments.get("command") or ""))
            except ValueError:
                parts = []
            if parts and parts[0].lower() == "pytest":
                validation_kind = "pytest"
            elif len(parts) >= 3 and parts[1:3] in [
                ["-m", "pytest"],
                ["-m", "unittest"],
            ]:
                validation_kind = parts[2].lower()
        if not validation_kind:
            return None

        normalized_observation_text = observation.content.lower()
        validation_environment_unavailable = any(
            marker in normalized_observation_text
            for marker in [
                "validation_blocked",
                "missing dependency",
                "no module named",
            ]
        )
        if validation_environment_unavailable:
            validation_status = "unavailable"
        elif observation.success:
            validation_status = "passed"
        else:
            validation_status = "failed"
        return {
            "kind": validation_kind,
            "status": validation_status,
            "tool": tool_name,
            "evidence": observation.content[:600],
        }

    @staticmethod
    def to_message_tool_call(call: ToolCall) -> dict[str, Any]:
        """转换为 OpenAI-compatible assistant message 结构。"""

        return {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": json.dumps(call.arguments, ensure_ascii=False),
            },
        }

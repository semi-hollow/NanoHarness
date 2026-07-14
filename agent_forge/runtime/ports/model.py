"""模型调用端口。"""

from __future__ import annotations

from typing import Protocol

from agent_forge.contracts import ToolSchema
from agent_forge.runtime.domain.conversation import AgentResponse, Message


class ModelPort(Protocol):
    """Application 发起一次规范化模型调用所需的最小能力。"""

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> AgentResponse:
        """返回 final answer、tool calls 或结构化 provider error。"""

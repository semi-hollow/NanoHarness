"""测试专用模型替身。

这里的对象只负责返回固定响应或按顺序返回响应，不模拟 Runtime 行为。
"""

from __future__ import annotations

from typing import Any

from agent_forge.runtime.domain.conversation import AgentResponse, Message


class StaticResponseModel:
    """始终返回同一最终回答，并保留最近一次模型输入供断言。"""

    last_usage = None

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0
        self.messages: list[Message] = []
        self.tools: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
    ) -> AgentResponse:
        self.calls += 1
        self.messages = list(messages)
        self.tools = list(tools)
        return AgentResponse(self.content, [])

    @property
    def tool_names(self) -> list[str]:
        """返回最近一次模型请求中可见的工具名称。"""

        return [str(tool.get("name") or "") for tool in self.tools]


class SequenceModel:
    """按给定顺序返回响应，用于重试、修复和 fallback 测试。"""

    def __init__(self, responses: list[AgentResponse]) -> None:
        self.responses = list(responses)
        self.messages: list[list[Message]] = []

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
    ) -> AgentResponse:
        self.messages.append(list(messages))
        return self.responses.pop(0)

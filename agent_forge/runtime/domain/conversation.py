"""模型与工具循环共享的内部协议对象。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Message:
    """进入模型端口的规范化消息。"""

    role: str
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    reasoning_content: str | None = None


@dataclass
class ToolCall:
    """从 provider wire format 归一化后的工具意图。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Observation:
    """所有工具返回给 Runtime 的统一结果。"""

    tool_name: str
    success: bool
    content: str


@dataclass
class AgentResponse:
    """模型端口返回的 final text、tool calls 或结构化错误。"""

    content: str | None
    tool_calls: list[ToolCall]
    error: dict[str, Any] | None = None
    reasoning_content: str | None = None
    usage: dict[str, Any] | None = None
    response_id: str | None = None
    normalization: dict[str, Any] | None = None

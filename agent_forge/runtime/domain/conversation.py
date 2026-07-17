"""模型与工具循环共享的内部协议对象。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# 核心数据：Runtime 传给 ModelPort 的 provider 无关消息。
@dataclass
class Message:
    """进入模型端口的规范化消息。"""

    role: str
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    reasoning_content: str | None = None


# 核心数据：模型请求执行某个工具的规范化意图。
@dataclass
class ToolCall:
    """从 provider wire format 归一化后的工具意图。"""

    id: str
    name: str
    arguments: dict[str, Any]


# 核心数据：任意工具返回 Runtime 的统一成功/失败结果。
@dataclass
class Observation:
    """所有工具返回给 Runtime 的统一结果。"""

    tool_name: str
    success: bool
    content: str


# 核心数据：ModelPort 返回的文本、工具意图、错误与用量事实。
@dataclass
class AgentResponse:
    """模型端口返回的 final text、tool calls 或结构化错误。

    ``content`` 与 ``tool_calls`` 是互补结果；``error`` 表示 provider/解析失败；
    ``reasoning_content`` 只作可选观测；usage、response_id 和 normalization 保存
    供应商用量、追踪标识和 tool-call 修复证据。
    """

    content: str | None
    tool_calls: list[ToolCall]
    error: dict[str, Any] | None = None
    reasoning_content: str | None = None
    usage: dict[str, Any] | None = None
    response_id: str | None = None
    normalization: dict[str, Any] | None = None

"""模型与工具循环共享的内部协议对象。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# 核心数据：Runtime 传给 ModelPort 的 provider 无关消息。
@dataclass
class Message:
    """进入模型端口的规范化消息，以及 Runtime-only journal provenance。

    ``item_id`` / ``turn_id`` / ``human_input_request_id`` 不会传给 Provider；它们让
    compaction 使用持久化身份判断 Turn authority 和 ask_human 事务，而不是比较文本。
    """

    role: str
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    reasoning_content: str | None = None
    origin: str = ""
    human_authority: bool = False
    item_id: str = ""
    turn_id: str = ""
    human_input_request_id: str = ""


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
    """所有工具返回给 Runtime 的统一结果。

    ``success`` 表示工具所检查的业务目标是否通过；``execution_succeeded``
    表示工具基础设施本身是否完成调用。大多数工具两者含义相同，因此后者默认
    为 ``None``。验证工具会显式区分“pytest 成功运行但测试未通过”和“pytest
    根本没有运行起来”，避免 Runtime 把正常的修复反馈误判成工具故障。
    ``validation_status`` 只由具有结构化验证契约的 Tool 设置，供 continuation
    state 直接消费 ``passed/failed/blocked``，不从 Observation 文本猜状态。
    """

    tool_name: str
    success: bool
    content: str
    execution_succeeded: bool | None = None
    validation_status: str | None = None

    def __post_init__(self) -> None:
        if self.validation_status not in {None, "passed", "failed", "blocked"}:
            raise ValueError(
                f"unsupported Observation validation_status: {self.validation_status}"
            )


# 核心数据：ModelPort 返回的文本、工具意图、错误与用量事实。
@dataclass
class AgentResponse:
    """模型端口返回的 final text、tool calls 或结构化错误。

    ``content`` 与 ``tool_calls`` 是互补结果；``error`` 表示 provider/解析失败；
    ``reasoning_content`` 只作可选观测；usage、response_id、observed_model 和
    normalization 保存供应商用量、追踪标识、响应模型标识和 tool-call
    修复证据。
    """

    content: str | None
    tool_calls: list[ToolCall]
    error: dict[str, Any] | None = None
    reasoning_content: str | None = None
    usage: dict[str, Any] | None = None
    response_id: str | None = None
    normalization: dict[str, Any] | None = None
    observed_model: str | None = None

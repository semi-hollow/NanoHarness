from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Message:
    """Chat message passed into LLM clients.

    `tool_calls` is optional so the same object supports OpenAI-compatible
    chat-completions protocol and local tool observations. Tool observations
    should follow an assistant message that contains the matching tool call id.
    """

    # Chat role: system, user, assistant, or tool.
    role: str

    # Text content. Assistant tool-call messages may have empty content.
    content: str

    # Tool name for tool-role messages.
    name: Optional[str] = None

    # Id linking a tool observation to the assistant tool call.
    tool_call_id: Optional[str] = None

    # OpenAI-compatible assistant tool_calls payload.
    tool_calls: Optional[list[dict[str, Any]]] = None

    # DeepSeek thinking-mode compatibility. When DeepSeek returns
    # reasoning_content with tool calls, its API requires the exact field in the
    # next request's assistant message. Other providers leave it unset.
    reasoning_content: Optional[str] = None

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Message:
    """Chat message passed into LLM clients.

    `tool_calls` is optional so the same object supports both the local mock
    model and OpenAI-compatible chat-completions protocol. Tool observations
    should follow an assistant message that contains the matching tool call id.
    """

    role: str
    content: str
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None

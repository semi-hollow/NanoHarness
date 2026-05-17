from dataclasses import dataclass
from typing import Optional


@dataclass
class Message:
    """Minimal chat message passed into LLM clients."""

    role: str
    content: str
    name: Optional[str] = None
    tool_call_id: Optional[str] = None

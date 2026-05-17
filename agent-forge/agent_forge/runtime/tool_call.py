from dataclasses import dataclass
from typing import Any


@dataclass
class ToolCall:
    """Normalized tool call parsed from MockLLM or OpenAI-compatible output."""

    id: str
    name: str
    arguments: dict[str, Any]

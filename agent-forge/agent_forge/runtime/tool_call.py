from dataclasses import dataclass
from typing import Any


@dataclass
class ToolCall:
    """Normalized tool call parsed from MockLLM or OpenAI-compatible output."""

    # Provider/tool-call id used to pair assistant call with tool observation.
    id: str

    # Local tool name.
    name: str

    # JSON-like arguments after provider parsing.
    arguments: dict[str, Any]

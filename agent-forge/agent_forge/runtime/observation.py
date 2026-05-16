from dataclasses import dataclass


@dataclass
class Observation:
    """Uniform result object returned by every tool execution."""

    tool_name: str
    success: bool
    content: str

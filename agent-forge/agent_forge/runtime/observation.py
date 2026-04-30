from dataclasses import dataclass


@dataclass
class Observation:
    tool_name: str
    success: bool
    content: str

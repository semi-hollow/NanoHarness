from dataclasses import dataclass
from typing import Any

@dataclass
class Message:
    role:str
    content:str

@dataclass
class ToolCall:
    name:str
    arguments:dict[str,Any]

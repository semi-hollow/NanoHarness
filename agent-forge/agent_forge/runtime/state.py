from dataclasses import dataclass, field

from agent_forge.runtime.message import Message
from agent_forge.runtime.observation import Observation


@dataclass
class AgentState:
    task: str
    workspace_root: str
    iteration: int = 0
    max_iterations: int = 12
    messages: list[Message] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    memory: dict[str, object] = field(default_factory=dict)
    status: str = "running"
    final_answer: str = ""
    stop_reason: str = ""

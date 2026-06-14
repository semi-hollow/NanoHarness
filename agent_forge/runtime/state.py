from dataclasses import dataclass, field

from agent_forge.runtime.message import Message
from agent_forge.runtime.observation import Observation


@dataclass
class AgentState:
    """Mutable state for one AgentLoop run.

    AgentLoop mostly writes trace directly, but keeping state explicit makes it
    clear what a production service would persist in a database for resume or
    replay.
    """

    # Original user task for this run.
    task: str

    # Workspace root for tools.
    workspace_root: str

    # Current loop iteration.
    iteration: int = 0

    # Configured loop cap.
    max_iterations: int = 12

    # Chat protocol messages accumulated during the run.
    messages: list[Message] = field(default_factory=list)

    # Tool observations collected during the run.
    observations: list[Observation] = field(default_factory=list)

    # Generic state bag for future extensions.
    memory: dict[str, object] = field(default_factory=dict)

    # running/completed/stopped/failed.
    status: str = "running"

    # Final answer if completed.
    final_answer: str = ""

    # Machine-readable stop reason.
    stop_reason: str = ""

from dataclasses import dataclass


@dataclass
class RuntimeConfig:
    """Runtime knobs passed from CLI into AgentLoop."""

    workspace: str
    max_steps: int = 12
    auto_approve_writes: bool = True
    trace_file: str = "agent_forge_trace.json"

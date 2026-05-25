from dataclasses import dataclass


@dataclass
class Observation:
    """Uniform result object returned by every tool execution."""

    # Tool that produced this observation.
    tool_name: str

    # Runtime success flag used by recovery/metrics.
    success: bool

    # Human/model-readable evidence fed into the next LLM turn.
    content: str

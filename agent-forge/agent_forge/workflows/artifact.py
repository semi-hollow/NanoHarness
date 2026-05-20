from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskArtifact:
    """Structured output contract between scheduled agent workers.

    This is the piece that separates an interview-grade multi-agent system from
    a chain of print statements. Workers should hand over typed artifacts:
    plans, patches, diagnostics, review findings, risk summaries, or reports.
    The data field stays generic so the project remains lightweight, but every
    artifact still has an explicit kind, owner, summary, and optional file list.
    """

    # Artifact type, for example agent_result, plan, patch, diagnostics, review.
    kind: str

    # Producing agent.
    owner: str

    # Short human-readable explanation.
    summary: str

    # Files this artifact is about.
    files: list[str] = field(default_factory=list)

    # Extra machine-readable payload.
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-safe artifact data for trace/session reports."""

        return {
            "kind": self.kind,
            "owner": self.owner,
            "summary": self.summary,
            "files": self.files,
            "data": self.data,
        }

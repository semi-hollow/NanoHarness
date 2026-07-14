from dataclasses import dataclass
from typing import Protocol


class ObservationView(Protocol):

    tool_name: str
    content: str
    success: bool


@dataclass(frozen=True)
class EvidenceItem:

    source: str
    summary: str
    kind: str = "tool"
    success: bool = True

    def citation(self) -> str:

        status = "ok" if self.success else "fail"
        return f"{self.kind}:{self.source}:{status}:{self.summary}"


class EvidenceLedger:

    def __init__(self) -> None:

        self.items: list[EvidenceItem] = []

    def add_observation(self, observation: ObservationView) -> EvidenceItem | None:

        text = observation.content or ""
        source = observation.tool_name
        summary = text.splitlines()[0][:160] if text else ""
        if observation.tool_name == "read_file" and text.startswith("path="):
            source = text.splitlines()[0].replace("path=", "", 1)
            summary = "file inspected"
        elif observation.tool_name == "run_command":
            summary = text.replace("\n", " ")[:160]
        elif observation.tool_name in {"apply_patch", "write_file"}:
            summary = text[:160]
        elif observation.tool_name in {"git_diff", "git_status", "diagnostics"}:
            summary = text.replace("\n", " ")[:160]
        elif not observation.success:
            summary = text[:160]
        else:
            return None

        item = EvidenceItem(source=source, summary=summary, kind=observation.tool_name, success=observation.success)
        self.items.append(item)
        return item

    def final_citations(self, limit: int = 5) -> list[str]:

        return [item.citation() for item in self.items[-limit:]]

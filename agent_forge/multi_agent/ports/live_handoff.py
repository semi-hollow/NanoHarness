"""Ports for the cooperative Live Handoff mechanism."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from ..domain.fanout import SubagentTask
from ..domain.live_handoff import (
    LiveHandoffEvent,
    LiveHandoffSummary,
    LiveWorkerCandidate,
)


class LiveWorkerContextPort(Protocol):
    """The only coordination surface visible to a running worker."""

    @property
    def task_id(self) -> str:
        """Return the stable task identity bound by the Runtime."""

        ...

    def publish(self, event: LiveHandoffEvent) -> bool:
        """Propose a structured event; the Runtime still validates it."""

        ...

    def drain_mailbox(self, *, boundary: str) -> list[LiveHandoffEvent]:
        """Consume messages only at a named safe turn boundary."""

        ...

    def record_action(self, action: str, **data: Any) -> None:
        """Record a bounded worker action for the mechanism timeline."""

        ...


class LiveHandoffWorkerPort(Protocol):
    """Run one isolated cooperative worker to a terminal candidate."""

    def run_worker(
        self,
        task: SubagentTask,
        context: LiveWorkerContextPort,
    ) -> LiveWorkerCandidate:
        """Execute turns and use ``context`` only between model/tool transactions."""

        ...


class LiveIntegrationPort(Protocol):
    """Validate the combined candidates after dependency freshness checks."""

    def validate(
        self,
        candidates: Mapping[str, LiveWorkerCandidate],
    ) -> tuple[bool, str]:
        """Return a real final test result and concise evidence."""

        ...


class LiveHandoffArtifactPort(Protocol):
    """Persist append-only timeline records and the final summary projection."""

    def append_timeline(self, record: Mapping[str, Any]) -> None:
        """Append and flush one immutable timeline record."""

        ...

    def write_summary(self, summary: LiveHandoffSummary) -> str:
        """Atomically write the final canonical summary and return its path."""

        ...

    def close(self) -> None:
        """Flush and close the per-run timeline writer."""

        ...

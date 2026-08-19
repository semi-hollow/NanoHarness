"""Process-local durable artifacts for one Live Handoff run."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any, Mapping, TextIO

from agent_forge.atomic_json import atomic_write_json

from ..domain.live_handoff import LiveHandoffSummary
from ..ports import LiveHandoffArtifactPort


class JsonlLiveHandoffRepository(LiveHandoffArtifactPort):
    """Append one flushed timeline record per line and atomically publish summary."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.timeline_path = self.root / "timeline.jsonl"
        self.summary_path = self.root / "summary.json"
        self._lock = Lock()
        self._timeline: TextIO | None = self.timeline_path.open(
            "w",
            encoding="utf-8",
        )

    def append_timeline(self, record: Mapping[str, Any]) -> None:
        with self._lock:
            if self._timeline is None:
                raise RuntimeError("live handoff timeline is already closed")
            self._timeline.write(
                json.dumps(dict(record), ensure_ascii=False, sort_keys=True)
            )
            self._timeline.write("\n")
            self._timeline.flush()

    def write_summary(self, summary: LiveHandoffSummary) -> str:
        atomic_write_json(self.summary_path, summary.to_dict())
        return str(self.summary_path)

    def close(self) -> None:
        with self._lock:
            if self._timeline is None:
                return
            self._timeline.flush()
            self._timeline.close()
            self._timeline = None

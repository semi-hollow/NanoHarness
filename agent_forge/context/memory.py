from __future__ import annotations

from typing import Any

from agent_forge.runtime.domain.conversation import Observation
from .memory_policy import MemoryPolicy, MemoryRecord


class Memory:

    def __init__(self, n: int = 8) -> None:

        self.items: list[object] = []
        self.observations: list[Observation] = []
        self.summaries: list[str] = []
        self.store: dict[str, object] = {}
        self.records: list[MemoryRecord] = []
        self.policy = MemoryPolicy()
        self.n = n

    def add(self, item: object) -> None:

        self.items = (self.items + [item])[-self.n:]

    def recent(self) -> list[object]:

        return list(self.items)

    def set(
        self,
        key: str,
        value: object,
        *,
        scope: str = "session",
        confidence: float = 1.0,
        ttl_seconds: float | None = None,
        source: str = "runtime",
        agent_name: str = "agent",
    ) -> None:

        self.store[key] = value
        self.records.append(
            MemoryRecord(
                key=str(key),
                value=str(value),
                scope=scope,
                confidence=confidence,
                ttl_seconds=ttl_seconds,
                source=source,
                agent_name=agent_name,
            )
        )

    def seed_session(self, previous_task: str = "", session_summary: str = "") -> None:

        if previous_task:
            self.set("previous_task", previous_task, scope="session", source="resume")
        if session_summary:
            self.summaries.append(session_summary)
            self.records.append(
                MemoryRecord(
                    key="session_summary",
                    value=session_summary,
                    scope="session",
                    confidence=0.8,
                    source="resume",
                )
            )

    def get(self, key: str, default: object = None) -> object:

        return self.store.get(key, default)

    def add_observation(self, observation: Observation | str) -> None:

        if isinstance(observation, Observation):
            obs = observation
        else:
            obs = Observation("memory", True, str(observation))
        self.observations.append(obs)
        if len(self.observations) > self.n:
            self._compact_oldest_observation()

    def recent_observations(self) -> list[Observation]:

        return list(self.observations)

    def clear(self) -> None:

        self.items.clear()
        self.observations.clear()
        self.summaries.clear()
        self.store.clear()
        self.records.clear()

    def summary(self, max_chars: int = 800, agent_name: str = "agent") -> str:

        recent = "; ".join(str(x) for x in self.items)
        obs = "; ".join(f"{o.tool_name}:{'ok' if o.success else 'fail'}:{o.content[:80]}" for o in self.observations)
        kv = ", ".join(f"{k}={v}" for k, v in self.store.items())
        summaries = "; ".join(self.summaries[-3:])
        scoped = "; ".join(
            f"{record.scope}:{record.key}={record.value[:80]}"
            for record in self.policy.visible_records(self.records, agent_name=agent_name)[-5:]
        )
        text = " | ".join(part for part in [summaries, recent, obs, kv, scoped] if part)
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 14] + " [compressed]"

    def _compact_oldest_observation(self) -> None:

        if not self.observations:
            return
        oldest = self.observations.pop(0)
        note = f"{oldest.tool_name}:{'ok' if oldest.success else 'fail'}:{oldest.content[:120]}"
        self.summaries = (self.summaries + [note])[-5:]

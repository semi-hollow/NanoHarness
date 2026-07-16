from __future__ import annotations

from agent_forge.context.domain import LongTermMemoryRecord
from agent_forge.runtime.domain.conversation import Observation


class Memory:
    """一次 run 的易失 working memory，以及已召回的长期记忆视图。"""

    def __init__(self, n: int = 8) -> None:
        self.items: list[object] = []
        self.observations: list[Observation] = []
        self.summaries: list[str] = []
        self.store: dict[str, object] = {}
        self.long_term_records: list[LongTermMemoryRecord] = []
        self.n = n

    def add(self, item: object) -> None:
        self.items = (self.items + [item])[-self.n:]

    def recent(self) -> list[object]:
        return list(self.items)

    def set(self, key: str, value: object) -> None:
        """保存当前 run 内的显式键值，不冒充长期记忆。"""

        self.store[key] = value

    def seed_session(self, previous_task: str = "", session_summary: str = "") -> None:
        if previous_task:
            self.set("previous_task", previous_task)
        if session_summary:
            self.summaries.append(session_summary)

    def seed_long_term(self, records: list[LongTermMemoryRecord]) -> None:
        """注入本次任务已经通过 policy 的只读召回结果。"""

        self.long_term_records = list(records)

    def long_term(self) -> list[LongTermMemoryRecord]:
        """返回隔离后的长期记忆视图。"""

        return list(self.long_term_records)

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
        self.long_term_records.clear()

    def summary(self, max_chars: int = 800) -> str:
        """压缩 working memory；长期记忆由独立区段渲染。"""

        recent = "; ".join(str(x) for x in self.items)
        obs = "; ".join(f"{o.tool_name}:{'ok' if o.success else 'fail'}:{o.content[:80]}" for o in self.observations)
        kv = ", ".join(f"{k}={v}" for k, v in self.store.items())
        summaries = "; ".join(self.summaries[-3:])
        text = " | ".join(part for part in [summaries, recent, obs, kv] if part)
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 14] + " [compressed]"

    def _compact_oldest_observation(self) -> None:
        if not self.observations:
            return
        oldest = self.observations.pop(0)
        note = f"{oldest.tool_name}:{'ok' if oldest.success else 'fail'}:{oldest.content[:120]}"
        self.summaries = (self.summaries + [note])[-5:]

from agent_forge.runtime.observation import Observation
from .memory_policy import MemoryPolicy, MemoryRecord


class Memory:
    """Short-term, summary, and session memory used by AgentLoop.

    The project keeps memory local and readable, but models the production
    design: recent facts stay as short-term memory, older observations are
    compressed into summary memory, and previous-session facts can be seeded
    when the user resumes a task.
    """

    def __init__(self, n=8):
        """Keep bounded recent memory so context growth is controlled."""

        self.items = []
        self.observations: list[Observation] = []
        self.summaries: list[str] = []
        self.store = {}
        self.records: list[MemoryRecord] = []
        self.policy = MemoryPolicy()
        self.n = n

    def add(self, item):
        """Add a lightweight note that can be rendered into future context."""

        self.items = (self.items + [item])[-self.n:]

    def recent(self):
        """Return recent lightweight notes for context construction."""

        return list(self.items)

    def set(
        self,
        key,
        value,
        *,
        scope: str = "session",
        confidence: float = 1.0,
        ttl_seconds: float | None = None,
        source: str = "runtime",
        agent_name: str = "agent",
    ):
        """Store a stable fact, such as the current task."""

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
        """Load previous run context without forcing it into every prompt.

        ContextStrategy decides whether this memory should be inherited for the
        current user task. This prevents the common multi-turn bug where stale
        history pollutes an unrelated request.
        """

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

    def get(self, key, default=None):
        """Read a stored fact without raising if it is absent."""

        return self.store.get(key, default)

    def add_observation(self, observation: Observation | str):
        """Append a tool observation so the next LLM turn sees recent results."""

        if isinstance(observation, Observation):
            obs = observation
        else:
            obs = Observation("memory", True, str(observation))
        self.observations.append(obs)
        if len(self.observations) > self.n:
            self._compact_oldest_observation()

    def recent_observations(self):
        """Return recent Observation objects for tests or richer context."""

        return list(self.observations)

    def clear(self):
        """Reset memory between runs/tests."""

        self.items.clear()
        self.observations.clear()
        self.summaries.clear()
        self.store.clear()
        self.records.clear()

    def summary(self, max_chars: int = 800, agent_name: str = "agent"):
        """Render notes, observations, and facts into a compact string."""

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
        """Compress the oldest observation into summary memory.

        This is the minimal version of conversation compaction: keep enough
        detail to explain what happened, but free the prompt from a growing list
        of raw tool outputs.
        """

        if not self.observations:
            return
        oldest = self.observations.pop(0)
        note = f"{oldest.tool_name}:{'ok' if oldest.success else 'fail'}:{oldest.content[:120]}"
        self.summaries = (self.summaries + [note])[-5:]

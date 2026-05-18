from agent_forge.runtime.observation import Observation


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
        self.n = n

    def add(self, item):
        """Add a lightweight note that can be rendered into future context."""

        self.items = (self.items + [item])[-self.n:]

    def recent(self):
        """Return recent lightweight notes for context construction."""

        return list(self.items)

    def set(self, key, value):
        """Store a stable fact, such as the current task."""

        self.store[key] = value

    def seed_session(self, previous_task: str = "", session_summary: str = "") -> None:
        """Load previous run context without forcing it into every prompt.

        ContextStrategy decides whether this memory should be inherited for the
        current user task. This prevents the common multi-turn bug where stale
        history pollutes an unrelated request.
        """

        if previous_task:
            self.store["previous_task"] = previous_task
        if session_summary:
            self.summaries.append(session_summary)

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

    def summary(self, max_chars: int = 800):
        """Render notes, observations, and facts into a compact string."""

        recent = "; ".join(str(x) for x in self.items)
        obs = "; ".join(f"{o.tool_name}:{'ok' if o.success else 'fail'}:{o.content[:80]}" for o in self.observations)
        kv = ", ".join(f"{k}={v}" for k, v in self.store.items())
        summaries = "; ".join(self.summaries[-3:])
        text = " | ".join(part for part in [summaries, recent, obs, kv] if part)
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

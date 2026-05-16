from agent_forge.runtime.observation import Observation


class Memory:
    """Small in-memory store for recent task facts and observations."""

    def __init__(self, n=5):
        """Keep only the last `n` notes/observations to control context size."""

        self.items = []
        self.observations: list[Observation] = []
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

    def get(self, key, default=None):
        """Read a stored fact without raising if it is absent."""

        return self.store.get(key, default)

    def add_observation(self, observation: Observation | str):
        """Append a tool observation so the next LLM turn sees recent results."""

        if isinstance(observation, Observation):
            obs = observation
        else:
            obs = Observation("memory", True, str(observation))
        self.observations = (self.observations + [obs])[-self.n:]

    def recent_observations(self):
        """Return recent Observation objects for tests or richer context."""

        return list(self.observations)

    def clear(self):
        """Reset memory between runs/tests."""

        self.items.clear()
        self.observations.clear()
        self.store.clear()

    def summary(self):
        """Render notes, observations, and facts into a compact string."""

        recent = "; ".join(str(x) for x in self.items)
        obs = "; ".join(f"{o.tool_name}:{'ok' if o.success else 'fail'}:{o.content[:80]}" for o in self.observations)
        kv = ", ".join(f"{k}={v}" for k, v in self.store.items())
        return " | ".join(part for part in [recent, obs, kv] if part)

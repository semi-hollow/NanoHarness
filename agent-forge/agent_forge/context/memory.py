from agent_forge.runtime.observation import Observation


class Memory:
    def __init__(self, n=5):
        self.items = []
        self.observations: list[Observation] = []
        self.store = {}
        self.n = n

    def add(self, item):
        self.items = (self.items + [item])[-self.n:]

    def recent(self):
        return list(self.items)

    def set(self, key, value):
        self.store[key] = value

    def get(self, key, default=None):
        return self.store.get(key, default)

    def add_observation(self, observation: Observation | str):
        if isinstance(observation, Observation):
            obs = observation
        else:
            obs = Observation("memory", True, str(observation))
        self.observations = (self.observations + [obs])[-self.n:]

    def recent_observations(self):
        return list(self.observations)

    def clear(self):
        self.items.clear()
        self.observations.clear()
        self.store.clear()

    def summary(self):
        recent = "; ".join(str(x) for x in self.items)
        obs = "; ".join(f"{o.tool_name}:{'ok' if o.success else 'fail'}:{o.content[:80]}" for o in self.observations)
        kv = ", ".join(f"{k}={v}" for k, v in self.store.items())
        return " | ".join(part for part in [recent, obs, kv] if part)

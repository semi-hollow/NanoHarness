from agent_forge.runtime.observation import Observation


class Tool:
    name: str = ""
    description: str = ""

    def schema(self) -> dict:
        raise NotImplementedError

    def execute(self, arguments: dict) -> Observation:
        raise NotImplementedError

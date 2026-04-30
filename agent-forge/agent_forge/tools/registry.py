from agent_forge.runtime.observation import Observation


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, tool):
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def get(self, name: str):
        return self._tools.get(name)

    def execute(self, name: str, arguments: dict) -> Observation:
        t = self.get(name)
        if not t:
            return Observation(name, False, f"unknown tool: {name}")
        validation_error = self._validate_arguments(t, arguments or {})
        if validation_error:
            return Observation(name, False, validation_error)
        try:
            return t.execute(arguments)
        except Exception as e:
            return Observation(name, False, f"tool execution error: {e}")

    def _validate_arguments(self, tool, arguments: dict) -> str:
        schema = tool.schema()
        required = schema.get("required")
        if required is None:
            required = list(schema.get("arguments", {}).keys())
        missing = [name for name in required if name not in arguments]
        if missing:
            return f"invalid arguments: missing {', '.join(missing)}"
        return ""

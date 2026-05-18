from agent_forge.runtime.observation import Observation


class ToolRegistry:
    """Registry boundary between model-generated tool calls and local tools."""

    def __init__(self):
        """Start with an empty tool map; `cli.build_registry` fills it."""

        self._tools = {}

    def register(self, tool):
        """Expose one tool by name so the LLM can request it later."""

        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        """Return all tool schemas that will be included in the LLM call."""

        return [t.schema() for t in self._tools.values()]

    def get(self, name: str):
        """Look up a tool without executing it; used by guardrails first."""

        return self._tools.get(name)

    def execute(self, name: str, arguments: dict) -> Observation:
        """Validate and execute one tool call, converting failures to Observation."""

        tool = self.get(name)
        if not tool:
            return Observation(name, False, f"unknown tool: {name}")
        validation_error = self._validate_arguments(tool, arguments or {})
        if validation_error:
            return Observation(name, False, validation_error)
        try:
            return tool.execute(arguments)
        except Exception as e:
            return Observation(name, False, f"tool execution error: {e}")

    def _validate_arguments(self, tool, arguments: dict) -> str:
        """Catch missing or obviously mistyped arguments before tools run."""

        schema = tool.schema()
        required = schema.get("required")
        if required is None:
            required = list(schema.get("arguments", {}).keys())
        missing = [name for name in required if name not in arguments]
        if missing:
            return f"invalid arguments: missing {', '.join(missing)}"
        expected = schema.get("arguments", {})
        for name, typ in expected.items():
            if name not in arguments:
                continue
            if not self._matches_type(arguments[name], typ):
                return f"invalid arguments: {name} must be {typ}"
        return ""

    def _matches_type(self, value, typ: str) -> bool:
        """Small schema checker that keeps bad tool calls inside the loop."""

        if isinstance(typ, dict):
            typ = typ.get("type", "object")
        if typ in {"str", "string"}:
            return isinstance(value, str)
        if typ in {"int", "integer"}:
            return isinstance(value, int) and not isinstance(value, bool)
        if typ in {"float", "number"}:
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if typ in {"bool", "boolean"}:
            return isinstance(value, bool)
        if typ in {"list", "array"}:
            return isinstance(value, list)
        if typ in {"dict", "object"}:
            return isinstance(value, dict)
        return True

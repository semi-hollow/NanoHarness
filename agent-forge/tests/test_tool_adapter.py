import unittest

from agent_forge.tools.adapters.mcp_style_adapter import MCPStyleToolAdapter, MCPStyleToolSpec
from agent_forge.tools.registry import ToolRegistry


class TestMCPStyleToolAdapter(unittest.TestCase):
    def test_mock_external_tool_executes_through_registry(self):
        spec = MCPStyleToolSpec(
            name="mock_lookup",
            description="mock external lookup",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        tool = MCPStyleToolAdapter(spec, lambda args: f"found:{args['query']}").to_tool()
        registry = ToolRegistry()
        registry.register(tool)
        observation = registry.execute("mock_lookup", {"query": "agent"})
        self.assertTrue(observation.success)
        self.assertEqual(observation.content, "found:agent")

    def test_missing_required_argument_returns_observation(self):
        spec = MCPStyleToolSpec("mock_lookup", "mock", {"required": ["query"], "properties": {}})
        observation = MCPStyleToolAdapter(spec, lambda args: "never").to_tool().execute({})
        self.assertFalse(observation.success)
        self.assertIn("missing query", observation.content)

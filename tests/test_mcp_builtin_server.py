import json
import sys
import unittest

from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.mcp_config import MCPConfigLoader
from agent_forge.tools.mcp_stdio import MCPStdioClient, MCPStdioServerSpec
from agent_forge.tools.registry import ToolRegistry


class BuiltinMCPServerTests(unittest.TestCase):
    def test_builtin_server_discovers_and_calls_offline_tools(self):
        spec = MCPStdioServerSpec(
            name="forge",
            command=sys.executable,
            args=["-m", "agent_forge.mcp.builtin_server", "--workspace", "."],
            cwd=".",
            env={"AGENT_FORGE_WEB_PROVIDER": "offline"},
        )
        client = MCPStdioClient(spec)

        tools = client.discover_tools()
        names = {tool["name"] for tool in tools}
        self.assertTrue({"repo_policy", "current_time", "web_search", "web_fetch"}.issubset(names))

        result = client.call_tool("web_search", {"query": "agent tool protocol", "max_results": 1})
        text = "\n".join(item.get("text", "") for item in result.get("content", []) if isinstance(item, dict))
        self.assertIn("provider: offline", text)
        self.assertIn("agent tool protocol", text)

    def test_example_config_registers_builtin_server_tools(self):
        registry = ToolRegistry()
        report = MCPConfigLoader(WorkspaceSandbox(".")).load_into(registry, "mcp_tools.example.json")
        registered = {row.name for row in report.tools if row.registered}

        self.assertTrue({"forge.repo_policy", "forge.current_time", "forge.web_search", "forge.web_fetch"}.issubset(registered))
        observation = registry.execute("forge.current_time", {})
        self.assertTrue(observation.success)
        self.assertIn("utc_time:", observation.content)

    def test_builtin_server_direct_call_returns_json_rpc_shape(self):
        spec = MCPStdioServerSpec(
            name="forge",
            command=sys.executable,
            args=["-m", "agent_forge.mcp.builtin_server", "--workspace", "."],
            cwd=".",
            env={"AGENT_FORGE_WEB_PROVIDER": "offline"},
        )
        result = MCPStdioClient(spec).call_tool("repo_policy", {"topic": "command"})
        self.assertIsInstance(result.get("content"), list)
        self.assertNotIn("error", json.dumps(result))


if __name__ == "__main__":
    unittest.main()

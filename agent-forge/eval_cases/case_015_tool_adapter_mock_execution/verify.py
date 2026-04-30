from agent_forge.tools.adapters.mcp_style_adapter import MCPStyleToolAdapter, MCPStyleToolSpec
from agent_forge.tools.registry import ToolRegistry

spec = MCPStyleToolSpec("mock_lookup", "mock lookup", {"properties": {"query": {"type": "string"}}, "required": ["query"]})
registry = ToolRegistry()
registry.register(MCPStyleToolAdapter(spec, lambda args: "found:" + args["query"]).to_tool())
obs = registry.execute("mock_lookup", {"query": "agent"})
raise SystemExit(0 if obs.success and obs.content == "found:agent" else 1)

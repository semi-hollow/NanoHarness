from agent_forge.tools.adapters.mcp_style_adapter import MCPStyleToolAdapter, MCPStyleToolSpec

spec = MCPStyleToolSpec("mock_lookup", "mock", {"required": ["query"], "properties": {}})
obs = MCPStyleToolAdapter(spec, lambda args: "never").to_tool().execute({})
raise SystemExit(0 if (not obs.success and "missing query" in obs.content) else 1)

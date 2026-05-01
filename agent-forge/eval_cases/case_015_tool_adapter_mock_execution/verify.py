from agent_forge.tools.adapters.mcp_style_adapter import MCPStyleToolAdapter, MCPStyleToolSpec
from agent_forge.tools.registry import ToolRegistry
import json

spec = MCPStyleToolSpec("mock_lookup", "mock lookup", {"properties": {"query": {"type": "string"}}, "required": ["query"]})
registry = ToolRegistry()
registry.register(MCPStyleToolAdapter(spec, lambda args: "found:" + args["query"]).to_tool())
obs = registry.execute("mock_lookup", {"query": "agent"})
ok=obs.success and obs.content == "found:agent"
print(json.dumps({"task_success":ok,"test_pass":ok,"safety_violation":False,"notes":"MCP-style local adapter executed"}))
raise SystemExit(0 if ok else 1)

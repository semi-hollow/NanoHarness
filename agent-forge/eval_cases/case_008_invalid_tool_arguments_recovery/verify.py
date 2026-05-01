from agent_forge.tools.adapters.mcp_style_adapter import MCPStyleToolAdapter, MCPStyleToolSpec
import json

spec = MCPStyleToolSpec("mock_lookup", "mock", {"required": ["query"], "properties": {}})
obs = MCPStyleToolAdapter(spec, lambda args: "never").to_tool().execute({})
ok=not obs.success and "missing query" in obs.content
print(json.dumps({"task_success":ok,"test_pass":True,"safety_violation":False,"notes":"invalid tool args returned failed observation"}))
raise SystemExit(0 if ok else 1)

from agent_forge.tools.registry import ToolRegistry

obs = ToolRegistry().execute("does_not_exist", {})
raise SystemExit(0 if (not obs.success and "unknown tool" in obs.content) else 1)

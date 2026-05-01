from agent_forge.tools.registry import ToolRegistry
import json

obs = ToolRegistry().execute("does_not_exist", {})
ok=not obs.success and "unknown tool" in obs.content
print(json.dumps({"task_success":ok,"test_pass":True,"safety_violation":False,"notes":"unknown tool returned failed observation"}))
raise SystemExit(0 if ok else 1)

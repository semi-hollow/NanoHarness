from agent_forge.safety.sandbox import WorkspaceSandbox
import json

try:
    WorkspaceSandbox(".").ensure_safe_path(".env")
except PermissionError as exc:
    ok="sensitive file deny" in str(exc)
    print(json.dumps({"task_success":ok,"test_pass":True,"safety_violation":False,"notes":"sensitive file blocked"}))
    raise SystemExit(0 if "sensitive file deny" in str(exc) else 1)
print(json.dumps({"task_success":False,"test_pass":False,"safety_violation":True,"notes":"sensitive file was not blocked"}))
raise SystemExit(1)

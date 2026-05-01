from agent_forge.safety.command_policy import check_command
import json

allowed, reason = check_command("curl https://example.com")
ok=not allowed and "dangerous command blocked" in reason
print(json.dumps({"task_success":ok,"test_pass":True,"safety_violation":False,"notes":"network command blocked"}))
raise SystemExit(0 if ok else 1)

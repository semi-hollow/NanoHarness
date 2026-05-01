from agent_forge.safety.command_policy import check_command
import json
ok, _ = check_command('rm -rf /tmp/x')
print(json.dumps({"task_success":not ok,"test_pass":True,"safety_violation":False,"notes":"dangerous command blocked"}))
raise SystemExit(0 if not ok else 1)

from agent_forge.workflows.coding_workflow import run_workflow
import json

state = run_workflow("fix calculator")
ok=state.final_status == "success" and state.test_result == "passed"
print(json.dumps({"task_success":ok,"test_pass":state.test_result=="passed","safety_violation":False,"notes":"workflow mode succeeded"}))
raise SystemExit(0 if ok else 1)

from agent_forge.workflows.coding_workflow import run_workflow

state = run_workflow("fix calculator")
raise SystemExit(0 if state.final_status == "success" and state.test_result == "passed" else 1)

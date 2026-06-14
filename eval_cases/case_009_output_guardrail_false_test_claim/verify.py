from agent_forge.safety.guardrails import output_guardrail
import json

result = output_guardrail("测试通过\n未验证点: none", ran_tests=False, had_block=False)
ok=not result.passed and "claims test pass" in result.reason
print(json.dumps({"task_success":ok,"test_pass":True,"safety_violation":False,"notes":"false test claim blocked"}))
raise SystemExit(0 if ok else 1)

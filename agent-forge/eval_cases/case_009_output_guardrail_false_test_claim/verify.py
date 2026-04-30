from agent_forge.safety.guardrails import output_guardrail

result = output_guardrail("测试通过\n未验证点: none", ran_tests=False, had_block=False)
raise SystemExit(0 if (not result.passed and "claims test pass" in result.reason) else 1)

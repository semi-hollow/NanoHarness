from dataclasses import dataclass


@dataclass
class GuardrailResult:
    passed: bool
    reason: str
    severity: str
    category: str = "general"


def input_guardrail(task: str) -> GuardrailResult:
    checks = ["rm -rf", "删除", ".env", "id_rsa", "http://", "https://", "../"]
    for c in checks:
        if c in task:
            return GuardrailResult(False, f"blocked risky input: {c}", "high", "input")
    return GuardrailResult(True, "ok", "low", "input")


def output_guardrail(final_answer: str, ran_tests: bool, had_block: bool) -> GuardrailResult:
    if "测试通过" in final_answer and not ran_tests:
        return GuardrailResult(False, "claims test pass without execution", "high", "output")
    if had_block and "拦截" not in final_answer and "blocked" not in final_answer:
        return GuardrailResult(False, "hides safety block", "medium", "output")
    if "未验证" not in final_answer:
        return GuardrailResult(False, "missing unverified section", "low", "output")
    return GuardrailResult(True, "ok", "low", "output")


def tool_guardrail(tool_name: str, arguments: dict, exists: bool = True, repeated: bool = False) -> GuardrailResult:
    if not exists:
        return GuardrailResult(False, f"unknown tool: {tool_name}", "medium", "tool")
    if repeated:
        return GuardrailResult(False, f"repeated tool call: {tool_name}", "medium", "tool")
    if arguments is None or not isinstance(arguments, dict):
        return GuardrailResult(False, "tool arguments must be an object", "medium", "tool")
    return GuardrailResult(True, "ok", "low", "tool")

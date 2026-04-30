from dataclasses import dataclass


@dataclass
class GuardrailResult:
    passed: bool
    reason: str
    severity: str


def input_guardrail(task: str) -> GuardrailResult:
    checks = ["rm -rf", "删除", ".env", "id_rsa", "http://", "https://", "../"]
    for c in checks:
        if c in task:
            return GuardrailResult(False, f"blocked risky input: {c}", "high")
    return GuardrailResult(True, "ok", "low")


def output_guardrail(final_answer: str, ran_tests: bool, had_block: bool) -> GuardrailResult:
    if "测试通过" in final_answer and not ran_tests:
        return GuardrailResult(False, "claims test pass without execution", "high")
    if had_block and "拦截" not in final_answer and "blocked" not in final_answer:
        return GuardrailResult(False, "hides safety block", "medium")
    if "未验证" not in final_answer:
        return GuardrailResult(False, "missing unverified section", "low")
    return GuardrailResult(True, "ok", "low")

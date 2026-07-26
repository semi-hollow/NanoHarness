from dataclasses import dataclass

RISKY_INPUT_MARKERS = ("rm -rf", "删除", ".env", "id_rsa", "http://", "https://", "../")


@dataclass
class GuardrailResult:

    passed: bool
    reason: str
    severity: str
    category: str = "general"


def input_guardrail(task: str) -> GuardrailResult:
    """检查任务文本并留下风险提示，但不把“提到”误当成“执行”。

    用户任务、issue 和日志都可能合法包含 URL、敏感文件名或危险命令示例。真正的副作用
    授权由工具可见性、CommandPolicy、Sandbox 和 Approval 负责；这里仅记录命中的文本，
    供 trace 与后续策略观察。
    """

    observed_markers = tuple(marker for marker in RISKY_INPUT_MARKERS if marker in task)
    if observed_markers:
        return GuardrailResult(
            True,
            "risky text observed; tool policy remains authoritative: "
            + ", ".join(observed_markers),
            "medium",
            "input",
        )
    return GuardrailResult(True, "ok", "low", "input")


def sanitize_quoted_evidence(text: str) -> str:

    sanitized = str(text or "")
    for index, marker in enumerate(RISKY_INPUT_MARKERS, start=1):
        sanitized = sanitized.replace(marker, f"[quoted-risk-{index}]")
    return sanitized


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

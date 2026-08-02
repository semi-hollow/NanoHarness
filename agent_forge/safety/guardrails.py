"""AgentLoop 输入、工具意图和最终回答的轻量语义门禁。

它类似支付系统中的业务校验器：只返回结构化校验结果。路径、命令、审批等强约束由
Sandbox、CommandPolicy 和 Hook 治理链负责，不能把这里的文本检查当成安全边界。
"""

from dataclasses import dataclass

RISKY_INPUT_MARKERS = ("rm -rf", "删除", ".env", "id_rsa", "http://", "https://", "../")


@dataclass(frozen=True, kw_only=True)
class GuardrailResult:
    """一次门禁判断；关键字构造使布尔值、原因、等级和类别一眼可辨。"""

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
            passed=True,
            reason="risky text observed; tool policy remains authoritative: "
            + ", ".join(observed_markers),
            severity="medium",
            category="input",
        )
    return GuardrailResult(passed=True, reason="ok", severity="low", category="input")


def sanitize_quoted_evidence(text: str) -> str:
    """替换引用文本中的风险标记，避免日志内容被误读为当前指令。"""

    sanitized = str(text or "")
    for index, marker in enumerate(RISKY_INPUT_MARKERS, start=1):
        sanitized = sanitized.replace(marker, f"[quoted-risk-{index}]")
    return sanitized


def output_guardrail(
    final_answer: str, ran_tests: bool, had_block: bool
) -> GuardrailResult:
    if "测试通过" in final_answer and not ran_tests:
        return GuardrailResult(
            passed=False,
            reason="claims test pass without execution",
            severity="high",
            category="output",
        )
    if had_block and "拦截" not in final_answer and "blocked" not in final_answer:
        return GuardrailResult(
            passed=False,
            reason="hides safety block",
            severity="medium",
            category="output",
        )
    if "未验证" not in final_answer:
        return GuardrailResult(
            passed=False,
            reason="missing unverified section",
            severity="low",
            category="output",
        )
    return GuardrailResult(passed=True, reason="ok", severity="low", category="output")


def tool_guardrail(
    tool_name: str,
    arguments: dict,
    exists: bool = True,
) -> GuardrailResult:
    if not exists:
        return GuardrailResult(
            passed=False,
            reason=f"unknown tool: {tool_name}",
            severity="medium",
            category="tool",
        )
    if arguments is None or not isinstance(arguments, dict):
        return GuardrailResult(
            passed=False,
            reason="tool arguments must be an object",
            severity="medium",
            category="tool",
        )
    return GuardrailResult(passed=True, reason="ok", severity="low", category="tool")

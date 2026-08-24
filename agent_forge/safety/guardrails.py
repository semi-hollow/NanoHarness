"""AgentLoop 输入、工具意图和最终回答的轻量语义检查。

代码沿用 ``guardrail`` 名称，但这三个函数只生成结构化检查结果，不共同构成一层
安全门禁。当前输入风险词和最终回答检查用于记录证据；工具检查也只记录最小完整性，
本轮可见性由执行管线复核。执行许可由 ``ToolAuthorizationGate`` 决定，命令和路径
边界分别由 ``CommandPolicy``、``WorkspaceSandbox`` 与执行环境强制实施。

系统角色：给输入、ToolCall 形状与最终回答生成可观测语义检查结果；只有明确的
output claim violation 会参与最终质量门，它不是完整授权系统。
输入：文本/Tool 形状与已发生验证事实；输出：``GuardrailResult``。
"""

import re
from dataclasses import dataclass

RISKY_INPUT_MARKERS = ("rm -rf", "删除", ".env", "id_rsa", "http://", "https://", "../")


@dataclass(frozen=True, kw_only=True)
class GuardrailResult:
    """一次语义检查结果；关键字构造使布尔值、原因、等级和类别一眼可辨。"""

    passed: bool
    reason: str
    severity: str
    category: str = "general"


def input_guardrail(task: str) -> GuardrailResult:
    """检查任务文本并留下风险提示，但不把“提到”误当成“执行”。

    用户任务、issue 和日志都可能合法包含 URL、敏感文件名或危险命令示例。这里仅记录
    命中的文本，不据此阻断任务。真正的工具范围、执行许可和执行边界分别由 Router/
    执行管线、ToolAuthorizationGate、CommandPolicy/Sandbox/执行环境负责。
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
    """检查最终文本是否夸大验证结果或隐藏阻断。

    high-severity 的明确验证通过断言会阻止 accepted COMPLETED；
    medium/low 结果仍作为可观测证据，不声称这是完整自然语言 classifier。
    """

    # 只识别明确的中英文 test-pass 断言；不试图理解所有自然语言。
    explicit_test_pass_claim = "测试通过" in final_answer or bool(
        re.search(
            r"\b(?:all\s+)?tests?\s+(?:pass|passed|passing)\b",
            final_answer,
            flags=re.IGNORECASE,
        )
    )
    if explicit_test_pass_claim and not ran_tests:
        return GuardrailResult(
            passed=False,
            reason="explicit test-pass claim without governed validation evidence",
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
    """生成规范化 ToolCall 的最小完整性检查结果，不直接决定是否执行。

    Provider 格式由 ``ToolCallNormalizer`` 处理，本轮路由由执行管线强制复核，
    必填参数和类型由 ``ToolRegistry`` 在调用工具实现前校验；本函数的结果只写入 Trace。
    """

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

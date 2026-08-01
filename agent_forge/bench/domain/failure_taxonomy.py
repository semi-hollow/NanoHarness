"""基于最终证据、按明确优先级分类 benchmark 结果。

这是无文件 IO、无模型调用的领域规则，不是 Agent Tool。Application Service 负责读取
usage/trace，本模块只把输入证据映射成一个 ``FailureDiagnosis``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import BenchCaseResult

FAILURE_TAXONOMY_VERSION = "1.0"
FAILURE_DIAGNOSIS_SOURCE = "ordered_rule_taxonomy"


# 核心数据：一个互斥失败分类及其证据、影响和下一步。
@dataclass(frozen=True, kw_only=True)
class FailureDiagnosis:
    """面向 report/case study 的稳定诊断结果，而不是原始异常文本。

    强制使用关键字参数创建，调用处必须直接显示每段文本属于哪个字段。
    """

    failure_class: str
    summary: str
    evidence: list[str]
    next_actions: list[str]
    severity: str = "medium"
    impact: str = ""
    engineering_lesson: str = ""
    rule_id: str = ""
    source: str = FAILURE_DIAGNOSIS_SOURCE
    taxonomy_version: str = FAILURE_TAXONOMY_VERSION

    def __post_init__(self) -> None:
        """默认让稳定 failure class 同时充当命中的 rule id。"""

        if not self.rule_id:
            object.__setattr__(self, "rule_id", self.failure_class)


# 主要入口：按官方结果、环境、验证、Runtime 行为的顺序选择唯一分类。
def classify_case_result(
    result: BenchCaseResult,
    usage: dict[str, Any],
    trace: dict[str, Any],
) -> FailureDiagnosis:
    """返回第一条被证据满足的诊断；分支顺序本身就是分类优先级。"""

    # region 1. 证据归一化：把动态 JSON 收敛成规则使用的具名事实
    usage_summary = usage.get("summary") or {}
    stop_reason = str(usage.get("stop_reason") or trace.get("stop_reason") or "")
    final_answer = str(
        result.final_answer
        or usage.get("final_answer")
        or trace.get("final_answer")
        or ""
    )
    failed_tool_call_count = _to_int_or_zero(usage_summary.get("failed_tool_calls"))
    total_token_count = _to_int_or_zero(usage_summary.get("total_tokens"))
    tool_call_count = _to_int_or_zero(usage_summary.get("tool_calls"))
    llm_call_count = _to_int_or_zero(usage_summary.get("llm_calls"))
    selected_file_counts = [
        _to_int_or_zero((step.get("context") or {}).get("selected_files_count"))
        for step in usage.get("steps", [])
        if isinstance(step, dict) and step.get("context")
    ]
    max_selected_files = max(selected_file_counts) if selected_file_counts else 0
    diagnosis_evidence = [
        f"status={result.status}",
        f"eval={result.evaluation_status}",
        f"stop_reason={stop_reason or 'unknown'}",
        f"patch_chars={result.patch_chars}",
        f"llm_calls={llm_call_count}",
        f"tool_calls={tool_call_count}",
        f"failed_tool_calls={failed_tool_call_count}",
        f"total_tokens={total_token_count}",
        f"max_selected_files={max_selected_files}",
    ]
    if result.error:
        diagnosis_evidence.append(f"runner_error={result.error[:240]}")

    # 只用于匹配非结构化错误标记；真正的状态字段仍按结构化值判断。
    normalized_failure_text = " ".join(
        [
            result.status,
            result.evaluation_status,
            stop_reason,
            final_answer,
            result.error,
        ]
    ).lower()
    official_evaluation_status = result.official_evaluation_status
    if (
        official_evaluation_status == "not_evaluated"
        and result.evaluation_status.startswith("official_")
    ):
        official_evaluation_status = result.evaluation_status
    # endregion 1. 证据归一化结束

    # region 2. 权威结果与环境故障：Official evaluator 优先于本地和 Runtime 症状
    if official_evaluation_status == "official_resolved":
        return FailureDiagnosis(
            failure_class="official_resolved",
            summary="The official SWE-bench per-case report accepted the candidate patch.",
            evidence=diagnosis_evidence,
            next_actions=[],
            severity="low",
            impact="This case has explicit official correctness evidence.",
            engineering_lesson="Resolved claims should be backed by parsed per-case evaluator artifacts.",
        )

    # 2. Harness/环境不可用时，不能误归因到 Agent 推理或 patch correctness。
    if result.error:
        return FailureDiagnosis(
            failure_class="runner_or_environment_error",
            summary="Runner, checkout, provider, or local environment failed before the agent could produce reliable evidence.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Fix the runner/provider/environment error first, then re-run the same case."
            ],
            severity="high",
            impact="The run cannot isolate agent behavior until the harness environment is healthy.",
            engineering_lesson="Separate harness failures from agent reasoning failures before tuning prompts or tools.",
        )
    if (
        "validation_blocked" in normalized_failure_text
        or "missing dependency" in normalized_failure_text
        or "no module named" in normalized_failure_text
    ):
        return FailureDiagnosis(
            failure_class="validation_environment_unavailable",
            summary="Validation could not complete because the test environment or dependency set was unavailable.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Fix or document the validation environment, then re-run without changing the agent policy."
            ],
            severity="medium",
            impact="A candidate patch may be correct, but the validation environment cannot prove it locally.",
            engineering_lesson="Evaluation must distinguish code failure from environment failure so optimization targets stay accurate.",
        )
    if official_evaluation_status == "official_eval_error":
        return FailureDiagnosis(
            failure_class="official_eval_error",
            summary="The official SWE-bench harness or its environment failed before patch correctness could be judged.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Fix the official evaluation environment, then rerun without changing the agent patch."
            ],
            severity="high",
            impact="The run cannot distinguish patch correctness from harness, Docker, or dependency failure.",
            engineering_lesson="Official evaluation process failures must not be reported as patch rejection.",
        )
    if official_evaluation_status == "official_eval_failed":
        return FailureDiagnosis(
            failure_class="official_eval_failed",
            summary="The official SWE-bench harness completed and rejected the candidate patch for this case.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Read official per-case output and candidate_changes.diff together; "
                "add this case to regression before tuning."
            ],
            severity="high",
            impact="The generated patch did not satisfy benchmark correctness criteria.",
            engineering_lesson="Patch generation, local validation, and official resolution are different evidence levels.",
        )
    # endregion 2. 权威结果与环境故障结束

    # region 3. 候选证据：local pass 或 diff 只能证明候选存在，不能外推 official
    if result.local_validation_status == "passed":
        return FailureDiagnosis(
            failure_class="locally_verified_candidate",
            summary="Local test evidence passed for the candidate patch; official SWE-bench resolution is still not claimed.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Run official SWE-bench evaluation before reporting official resolved rate."
            ],
            severity="low",
            impact="The patch has local validation evidence but no official benchmark outcome.",
            engineering_lesson="Local and official validation should remain separate evidence levels.",
        )
    if result.patch_chars > 0:
        return FailureDiagnosis(
            failure_class="patch_generated_but_unverified",
            summary="The agent produced a candidate patch, but it should not be called resolved without validation evidence.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Run local validation or official SWE-bench evaluation before claiming solved."
            ],
            severity="low",
            impact="The runtime reached edit capability, but correctness remains unproven.",
            engineering_lesson="Conservative reporting prevents benchmark demos from becoming unsupported success claims.",
        )
    # endregion 3. 候选证据结束

    # region 4. Runtime 症状：没有 correctness 证据时再分析协议、窗口和工具行为
    if (
        "offset" in normalized_failure_text
        and "limit" in normalized_failure_text
        and (
            "ignored" in normalized_failure_text
            or "line window" in normalized_failure_text
        )
    ):
        return FailureDiagnosis(
            failure_class="tool_schema_mismatch",
            summary="The model attempted a natural tool-call shape that the runtime tool schema did not support correctly.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Align tool schema and coercion with common model invocation patterns, then replay the case."
            ],
            severity="high",
            impact="The agent can waste steps or inspect the wrong code even when the right intent is visible.",
            engineering_lesson="Agent tools are model-facing contracts; schema mismatch is an agent reliability bug, not just an API bug.",
        )
    if "pending_tool_call_at_stop" in normalized_failure_text:
        return FailureDiagnosis(
            failure_class="pending_tool_call_at_stop",
            summary="The model still requested a tool on the final turn, so the runtime blocked an incomplete artifact.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Inspect the final model action and increase budget or force an earlier patch/no-patch decision."
            ],
            severity="high",
            impact="The final answer is not trustworthy because the model had unfinished tool intent.",
            engineering_lesson="Final answers need runtime validation; unfinished tool calls should not be treated as completed work.",
        )
    if any(
        marker in normalized_failure_text
        for marker in (
            "context_length_exceeded",
            "maximum context length",
            "context window",
            "too many tokens",
            "prompt is too long",
        )
    ):
        return FailureDiagnosis(
            failure_class="context_window_exceeded",
            summary="The complete model request still exceeded the provider window after the available structured compaction path.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Inspect context_window events for static section size, tool-schema cost, safe compaction boundaries, and overflow recovery outcome."
            ],
            severity="high",
            impact="The model could not continue even if repository file selection itself was correct.",
            engineering_lesson="Full-request window failures must be separated from repository retrieval misses and transport instability.",
        )
    if any(
        marker in normalized_failure_text
        for marker in (
            "incompleteread",
            "request_failed",
            "request_timeout",
            "rate_limited",
            "server_error",
        )
    ):
        return FailureDiagnosis(
            failure_class="provider_transport_error",
            summary="The provider transport failed or returned an incomplete response before the agent finished.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Treat as provider instability; retry only after the client converts transport failures into structured observations."
            ],
            severity="high",
            impact="The failure says little about coding ability until transport errors are isolated.",
            engineering_lesson="Runtime observability should separate model/provider transport from agent logic failures.",
        )
    if "repeated" in normalized_failure_text:
        return FailureDiagnosis(
            failure_class="repeated_action_loop",
            summary="The loop collapsed into repeated or near-repeated tool use before producing a patch.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Use trace timeline to find the first repeated action and add recovery that forces a different observation path."
            ],
            severity="high",
            impact="The agent spent budget without gaining new information.",
            engineering_lesson="Loop control needs risk-aware repetition policy: repeated reads and repeated writes should not be handled identically.",
        )
    if (
        "input_guardrail_block" in normalized_failure_text
        or "blocked risky input" in normalized_failure_text
    ):
        return FailureDiagnosis(
            failure_class="input_policy_block",
            summary="The runtime blocked task text before the first model call.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Check whether the policy confused quoted issue/log content with an executable tool action."
            ],
            severity="high",
            impact="No agent capability was exercised, so the outcome cannot be attributed to context retrieval or model reasoning.",
            engineering_lesson="Task text is evidence and intent; side-effect authorization belongs at the tool boundary.",
        )
    if (
        "command blocked" in normalized_failure_text
        or "unsafe" in normalized_failure_text
        or "permission" in normalized_failure_text
    ):
        return FailureDiagnosis(
            failure_class="unsafe_or_blocked_command",
            summary="Command or permission policy blocked an unsafe or unsupported action.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Replace free-form shell behavior with python_validation or an explicit approval path."
            ],
            severity="medium",
            impact="The run preserved safety, but may need a better sanctioned validation path.",
            engineering_lesson="Tool governance should narrow side effects while still giving agents a valid path to complete work.",
        )
    if failed_tool_call_count > 0:
        return FailureDiagnosis(
            failure_class="tool_not_available",
            summary="One or more requested tools failed or were unavailable, and the agent did not recover into a patch.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Classify the failed tool as retryable, hidden-by-policy, or schema-invalid."
            ],
            severity="medium",
            impact="The agent's plan depended on an action that the runtime could not execute.",
            engineering_lesson="Tool availability and recovery policy are part of the agent control plane.",
        )
    if max_selected_files == 0 and result.status in {"blocked", "no_patch"}:
        return FailureDiagnosis(
            failure_class="context_miss",
            summary="The agent did not surface concrete source files before stopping.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Tune file ranking, symbol search, or external context retrieval for this case."
            ],
            severity="high",
            impact="The model likely lacked the code evidence needed to make a safe edit.",
            engineering_lesson="Context engineering should be evaluated by whether expected files appear before the agent commits to an action.",
        )
    # endregion 4. Runtime 症状结束

    # region 5. 保守兜底：无更具体证据时只报告 no-patch 或 unclassified
    if result.status == "no_patch":
        return FailureDiagnosis(
            failure_class="no_patch_generated",
            summary="The loop ended without producing a diff even though it was not explicitly blocked.",
            evidence=diagnosis_evidence,
            next_actions=[
                "Inspect the last two trace steps and require either a patch or a concrete blocker with evidence."
            ],
            severity="medium",
            impact="The agent did not reach the edit phase.",
            engineering_lesson="A useful harness explains no-patch outcomes instead of treating them as generic failure.",
        )
    return FailureDiagnosis(
        failure_class="unclassified",
        summary="No specific diagnosis matched. Keep the trace and usage artifacts for manual review.",
        evidence=diagnosis_evidence,
        next_actions=["Promote this pattern into a diagnosis rule if it repeats."],
        severity="low",
        impact="The current taxonomy does not yet cover this behavior.",
        engineering_lesson="Failure taxonomies should evolve from repeated bad cases, not from abstract labels alone.",
    )
    # endregion 5. 保守兜底结束


def _to_int_or_zero(value: object) -> int:
    """把来自 JSON 的计数字段转成整数；缺失或非法值按零处理。"""

    try:
        return int(str(value or 0))
    except (TypeError, ValueError):
        return 0

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import BenchCaseResult


@dataclass(frozen=True)
class FailureDiagnosis:

    failure_class: str
    summary: str
    evidence: list[str]
    next_actions: list[str]
    severity: str = "medium"
    impact: str = ""
    engineering_lesson: str = ""


def classify_case_result(result: BenchCaseResult, usage: dict[str, Any], trace: dict[str, Any]) -> FailureDiagnosis:
    summary = usage.get("summary") or {}
    stop_reason = str(usage.get("stop_reason") or trace.get("stop_reason") or "")
    final_answer = str(result.final_answer or usage.get("final_answer") or trace.get("final_answer") or "")
    failed_tools = _int(summary.get("failed_tool_calls"))
    total_tokens = _int(summary.get("total_tokens"))
    tool_calls = _int(summary.get("tool_calls"))
    llm_calls = _int(summary.get("llm_calls"))
    selected_file_counts = [
        _int((step.get("context") or {}).get("selected_files_count"))
        for step in usage.get("steps", [])
        if isinstance(step, dict) and step.get("context")
    ]
    max_selected_files = max(selected_file_counts) if selected_file_counts else 0
    evidence = [
        f"status={result.status}",
        f"eval={result.evaluation_status}",
        f"stop_reason={stop_reason or 'unknown'}",
        f"patch_chars={result.patch_chars}",
        f"llm_calls={llm_calls}",
        f"tool_calls={tool_calls}",
        f"failed_tool_calls={failed_tools}",
        f"total_tokens={total_tokens}",
        f"max_selected_files={max_selected_files}",
    ]
    if result.error:
        evidence.append(f"runner_error={result.error[:240]}")

    lowered = " ".join([result.status, result.evaluation_status, stop_reason, final_answer, result.error]).lower()
    official_status = result.official_evaluation_status
    if official_status == "not_evaluated" and result.evaluation_status.startswith("official_"):
        official_status = result.evaluation_status

    if official_status == "official_resolved":
        return FailureDiagnosis(
            "official_resolved",
            "The official SWE-bench per-case report accepted the candidate patch.",
            evidence,
            [],
            severity="low",
            impact="This case has explicit official correctness evidence.",
            engineering_lesson="Resolved claims should be backed by parsed per-case evaluator artifacts.",
        )

    if result.error:
        return FailureDiagnosis(
            "runner_or_environment_error",
            "Runner, checkout, provider, or local environment failed before the agent could produce reliable evidence.",
            evidence,
            ["Fix the runner/provider/environment error first, then re-run the same case."],
            severity="high",
            impact="The run cannot isolate agent behavior until the harness environment is healthy.",
            engineering_lesson="Separate harness failures from agent reasoning failures before tuning prompts or tools.",
        )
    if "validation_blocked" in lowered or "missing dependency" in lowered or "no module named" in lowered:
        return FailureDiagnosis(
            "validation_environment_unavailable",
            "Validation could not complete because the test environment or dependency set was unavailable.",
            evidence,
            ["Fix or document the validation environment, then re-run without changing the agent policy."],
            severity="medium",
            impact="A candidate patch may be correct, but the validation environment cannot prove it locally.",
            engineering_lesson="Evaluation must distinguish code failure from environment failure so optimization targets stay accurate.",
        )
    if official_status == "official_eval_error":
        return FailureDiagnosis(
            "official_eval_error",
            "The official SWE-bench harness or its environment failed before patch correctness could be judged.",
            evidence,
            ["Fix the official evaluation environment, then rerun without changing the agent patch."],
            severity="high",
            impact="The run cannot distinguish patch correctness from harness, Docker, or dependency failure.",
            engineering_lesson="Official evaluation process failures must not be reported as patch rejection.",
        )
    if official_status == "official_eval_failed":
        return FailureDiagnosis(
            "official_eval_failed",
            "The official SWE-bench harness completed and rejected the candidate patch for this case.",
            evidence,
            ["Read official per-case output and patch.diff together; add this case to regression before tuning."],
            severity="high",
            impact="The generated patch did not satisfy benchmark correctness criteria.",
            engineering_lesson="Patch generation, local validation, and official resolution are different evidence levels.",
        )
    if result.local_validation_status == "passed":
        return FailureDiagnosis(
            "locally_verified_candidate",
            "Local test evidence passed for the candidate patch; official SWE-bench resolution is still not claimed.",
            evidence,
            ["Run official SWE-bench evaluation before reporting official resolved rate."],
            severity="low",
            impact="The patch has local validation evidence but no official benchmark outcome.",
            engineering_lesson="Local and official validation should remain separate evidence levels.",
        )
    if result.patch_chars > 0:
        return FailureDiagnosis(
            "patch_generated_but_unverified",
            "The agent produced a candidate patch, but it should not be called resolved without validation evidence.",
            evidence,
            ["Run local diagnostics or official SWE-bench evaluation before claiming solved."],
            severity="low",
            impact="The runtime reached edit capability, but correctness remains unproven.",
            engineering_lesson="Conservative reporting prevents benchmark demos from becoming unsupported success claims.",
        )
    if "offset" in lowered and "limit" in lowered and ("ignored" in lowered or "line window" in lowered):
        return FailureDiagnosis(
            "tool_schema_mismatch",
            "The model attempted a natural tool-call shape that the runtime tool schema did not support correctly.",
            evidence,
            ["Align tool schema and coercion with common model invocation patterns, then replay the case."],
            severity="high",
            impact="The agent can waste steps or inspect the wrong code even when the right intent is visible.",
            engineering_lesson="Agent tools are model-facing contracts; schema mismatch is an agent reliability bug, not just an API bug.",
        )
    if "pending_tool_call_at_stop" in lowered:
        return FailureDiagnosis(
            "pending_tool_call_at_stop",
            "The model still requested a tool on the final turn, so the runtime blocked an incomplete artifact.",
            evidence,
            ["Inspect the final model action and increase budget or force an earlier patch/no-patch decision."],
            severity="high",
            impact="The final answer is not trustworthy because the model had unfinished tool intent.",
            engineering_lesson="Final answers need runtime validation; unfinished tool calls should not be treated as completed work.",
        )
    if "incompleteread" in lowered or "request_failed" in lowered:
        return FailureDiagnosis(
            "provider_transport_error",
            "The provider transport failed or returned an incomplete response before the agent finished.",
            evidence,
            ["Treat as provider instability; retry only after the client converts transport failures into structured observations."],
            severity="high",
            impact="The failure says little about coding ability until transport errors are isolated.",
            engineering_lesson="Runtime observability should separate model/provider transport from agent logic failures.",
        )
    if "repeated" in lowered:
        return FailureDiagnosis(
            "repeated_action_loop",
            "The loop collapsed into repeated or near-repeated tool use before producing a patch.",
            evidence,
            ["Use trace timeline to find the first repeated action and add recovery that forces a different observation path."],
            severity="high",
            impact="The agent spent budget without gaining new information.",
            engineering_lesson="Loop control needs risk-aware repetition policy: repeated reads and repeated writes should not be handled identically.",
        )
    if "command blocked" in lowered or "unsafe" in lowered or "permission" in lowered:
        return FailureDiagnosis(
            "unsafe_or_blocked_command",
            "Command or permission policy blocked an unsafe or unsupported action.",
            evidence,
            ["Replace free-form shell behavior with a narrower diagnostics tool or explicit approval path."],
            severity="medium",
            impact="The run preserved safety, but may need a better sanctioned validation path.",
            engineering_lesson="Tool governance should narrow side effects while still giving agents a valid path to complete work.",
        )
    if failed_tools > 0:
        return FailureDiagnosis(
            "tool_not_available",
            "One or more requested tools failed or were unavailable, and the agent did not recover into a patch.",
            evidence,
            ["Classify the failed tool as retryable, hidden-by-policy, or schema-invalid."],
            severity="medium",
            impact="The agent's plan depended on an action that the runtime could not execute.",
            engineering_lesson="Tool availability and recovery policy are part of the agent control plane.",
        )
    if max_selected_files == 0 and result.status in {"blocked", "no_patch"}:
        return FailureDiagnosis(
            "context_miss",
            "The agent did not surface concrete source files before stopping.",
            evidence,
            ["Tune file ranking, symbol search, or external context retrieval for this case."],
            severity="high",
            impact="The model likely lacked the code evidence needed to make a safe edit.",
            engineering_lesson="Context engineering should be evaluated by whether expected files appear before the agent commits to an action.",
        )
    if result.status == "no_patch":
        return FailureDiagnosis(
            "no_patch_generated",
            "The loop ended without producing a diff even though it was not explicitly blocked.",
            evidence,
            ["Inspect the last two trace steps and require either a patch or a concrete blocker with evidence."],
            severity="medium",
            impact="The agent did not reach the edit phase.",
            engineering_lesson="A useful harness explains no-patch outcomes instead of treating them as generic failure.",
        )
    return FailureDiagnosis(
        "unclassified",
        "No specific diagnosis matched. Keep the trace and usage artifacts for manual review.",
        evidence,
        ["Promote this pattern into a diagnosis rule if it repeats."],
        severity="low",
        impact="The current taxonomy does not yet cover this behavior.",
        engineering_lesson="Failure taxonomies should evolve from repeated bad cases, not from abstract labels alone.",
    )


def _int(value: object) -> int:
    try:
        return int(str(value or 0))
    except (TypeError, ValueError):
        return 0

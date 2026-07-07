from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .types import BenchCaseResult


@dataclass(frozen=True)
class FailureDiagnosis:
    """Human-readable diagnosis derived from benchmark artifacts.

    Why this exists:
        A production Agent team cannot stop at "the run failed". Reviewers want
        to know whether the failure came from context retrieval, tool execution,
        loop control, budget, provider config, or official evaluation. This read
        model turns trace/usage/result artifacts into an actionable triage row.
    """

    failure_class: str
    summary: str
    evidence: list[str]
    next_actions: list[str]
    severity: str = "medium"


def attach_failure_diagnosis(result: BenchCaseResult) -> BenchCaseResult:
    """Populate diagnosis fields on one mutable case result."""

    diagnosis = diagnose_case_result(result)
    result.failure_class = diagnosis.failure_class
    result.diagnosis = diagnosis.summary
    result.diagnosis_evidence = diagnosis.evidence
    result.next_actions = diagnosis.next_actions
    return result


def diagnose_case_result(result: BenchCaseResult) -> FailureDiagnosis:
    """Classify a case result using status, final answer, trace, and usage.

    The rules are intentionally explicit and conservative. They are not meant to
    hide failures; they make failures interview- and ops-readable so the next
    iteration has a target.
    """

    usage = _read_json(_usage_json_path(result.usage_report_path))
    trace = _read_json(result.trace_path)
    summary = usage.get("summary") or {}
    stop_reason = str(usage.get("stop_reason") or trace.get("stop_reason") or "")
    final_answer = str(result.final_answer or usage.get("final_answer") or trace.get("final_answer") or "")
    failed_tools = int(summary.get("failed_tool_calls") or 0)
    total_tokens = int(summary.get("total_tokens") or 0)
    tool_calls = int(summary.get("tool_calls") or 0)
    llm_calls = int(summary.get("llm_calls") or 0)
    selected_file_counts = [
        int((step.get("context") or {}).get("selected_files_count") or 0)
        for step in usage.get("steps", [])
        if step.get("context")
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

    if result.error:
        return FailureDiagnosis(
            "runner_or_environment_error",
            "Runner, checkout, provider, or local environment failed before the agent could produce a reliable patch.",
            evidence,
            [
                "Open the runner error first; fix dependency, checkout, provider, or command-policy issues before tuning the agent.",
                "Re-run the same fixed case after the environment is clean so the comparison stays meaningful.",
            ],
            severity="high",
        )
    if result.evaluation_status == "official_eval_failed":
        return FailureDiagnosis(
            "official_eval_failure",
            "The agent produced a candidate patch, but the official SWE-bench harness rejected it.",
            evidence,
            [
                "Read the official harness output and the generated patch.diff together.",
                "Add the failure to the regression set before changing prompt, retrieval, or tools.",
            ],
            severity="high",
        )
    if result.patch_chars > 0:
        return FailureDiagnosis(
            "candidate_patch_generated",
            "The agent produced a candidate patch. Treat it as evidence, not a resolved claim, until official evaluation passes.",
            evidence,
            [
                "Run with --evaluate on a Docker/SWE-bench-ready machine.",
                "Compare against direct_baseline_predictions.jsonl to justify AgentLoop cost.",
            ],
            severity="low",
        )
    if "pending_tool_call_at_stop" in lowered:
        return FailureDiagnosis(
            "pending_tool_call_at_stop",
            "The model still requested a tool on the final turn, so the runtime blocked an incomplete artifact instead of treating it as done.",
            evidence,
            [
                "Inspect the final llm_call event to see which tool was still pending.",
                "Prompt or policy should force patch/edit decisions before the final turn, or increase step budget for this case.",
            ],
            severity="high",
        )
    if "incompleteread" in lowered or "request_failed" in lowered:
        return FailureDiagnosis(
            "provider_transport_error",
            "The provider transport failed or returned an incomplete response before the agent could finish the role.",
            evidence,
            [
                "Treat this as model/provider transport instability before tuning retrieval or prompts.",
                "Retry the same case after confirming the LLM client converts transport failures into structured errors.",
            ],
            severity="high",
        )
    if "repeated" in lowered:
        return FailureDiagnosis(
            "loop_collapse_repeated_tool",
            "The loop collapsed into repeated or near-repeated tool use before producing a patch.",
            evidence,
            [
                "Improve observation summarization so the next LLM turn sees why the previous call was insufficient.",
                "Add a recovery branch that forces a different tool or asks the planner to restate missing evidence after repeated calls.",
                "Use the trace timeline to identify the first repeated action rather than only reading the final answer.",
            ],
            severity="high",
        )
    if "max_steps" in lowered or "budget" in lowered or "timeout" in lowered:
        return FailureDiagnosis(
            "runtime_budget_exhausted",
            "The runtime stopped the case because a step, cost, or time budget was exhausted.",
            evidence,
            [
                "Check Usage Dashboard for the most expensive step and context section.",
                "Reduce noisy context or add a more targeted retrieval step before increasing the budget.",
            ],
            severity="medium",
        )
    if failed_tools > 0:
        return FailureDiagnosis(
            "tool_failure_recovery_gap",
            "One or more tools failed and the agent did not recover into a patch.",
            evidence,
            [
                "Classify the failed tool as retryable or non-retryable.",
                "Add tool-specific recovery hints or schema tightening for the failing tool.",
            ],
            severity="medium",
        )
    if max_selected_files == 0 and result.status in {"blocked", "no_patch"}:
        return FailureDiagnosis(
            "context_retrieval_miss",
            "The agent did not surface concrete source files before stopping, so it likely lacked the right code context.",
            evidence,
            [
                "Tune file ranking, symbol search, or lexical retrieval for this case.",
                "Add expected files to the case note and verify they appear in context_assembly events.",
            ],
            severity="high",
        )
    if result.status == "no_patch":
        return FailureDiagnosis(
            "no_patch_generated",
            "The loop ended without producing a diff even though it was not explicitly blocked.",
            evidence,
            [
                "Inspect the final answer and last two steps in the trace.",
                "Tighten the task prompt to require either a patch or a concrete blocker with evidence.",
            ],
            severity="medium",
        )
    if result.status == "blocked":
        return FailureDiagnosis(
            "runtime_blocked_unknown",
            "The runtime blocked the case, but no more specific diagnosis rule matched.",
            evidence,
            [
                "Inspect guardrail, permission, hook_check, and recovery_decision events in the trace timeline.",
                "Add a new diagnosis rule if the same blocked pattern appears in multiple cases.",
            ],
            severity="medium",
        )
    return FailureDiagnosis(
        "unclassified",
        "No specific diagnosis matched. Keep the trace and usage artifacts for manual review.",
        evidence,
        ["Promote this pattern into a diagnosis rule if it repeats."],
        severity="low",
    )


def _usage_json_path(usage_report_path: Path | None) -> Path | None:
    """Infer usage.json path from usage_report.md path."""

    if not usage_report_path:
        return None
    path = Path(usage_report_path)
    if path.name == "usage_report.md":
        return path.with_name("usage.json")
    if path.suffix == ".md":
        return path.with_suffix(".json")
    return path


def _read_json(path: Path | None) -> dict[str, Any]:
    """Read JSON artifacts without making report generation fragile."""

    if not path or not Path(path).exists():
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"read_error": str(exc), "path": str(path)}

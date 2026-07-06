from __future__ import annotations

import json
from pathlib import Path

from .types import EvaluationComparison


def render_evaluation_report(comparison: EvaluationComparison) -> str:
    """Render an interview-readable single-vs-multi evidence card."""

    cost_delta = comparison.multi_cost_usd - comparison.single_cost_usd
    llm_delta = comparison.multi_llm_calls - comparison.single_llm_calls
    tool_delta = comparison.multi_tool_calls - comparison.single_tool_calls
    failed_tool_delta = comparison.multi_failed_tool_calls - comparison.single_failed_tool_calls
    quality_signal = _quality_signal(comparison)
    return "\n".join(
        [
            "# Single vs Multi-Agent Comparison",
            "",
            "## Executive Summary",
            "",
            f"- task_id: `{comparison.task_id}`",
            f"- quality_signal: {quality_signal}",
            f"- cost_delta_usd: `{cost_delta:.6f}`",
            "- official SWE-bench evaluation: read each case status; generated patches are not resolved-rate claims unless official eval ran.",
            f"- recommendation: {comparison.recommendation}",
            "",
            "## Side-by-Side Metrics",
            "",
            "| Metric | Single | Multi | Delta |",
            "| --- | ---: | ---: | ---: |",
            f"| patch generated | `{comparison.single_patch_generated}` | `{comparison.multi_patch_generated}` | `{comparison.multi_patch_generated != comparison.single_patch_generated}` |",
            f"| estimated cost USD | `{comparison.single_cost_usd:.6f}` | `{comparison.multi_cost_usd:.6f}` | `{cost_delta:.6f}` |",
            f"| LLM calls | `{comparison.single_llm_calls}` | `{comparison.multi_llm_calls}` | `{llm_delta}` |",
            f"| tool calls | `{comparison.single_tool_calls}` | `{comparison.multi_tool_calls}` | `{tool_delta}` |",
            f"| failed tool calls | `{comparison.single_failed_tool_calls}` | `{comparison.multi_failed_tool_calls}` | `{failed_tool_delta}` |",
            "",
            "## Multi-Agent Review Loop",
            "",
            f"- multi_status: `{comparison.multi_status or '-'}`",
            f"- revision_rounds: `{comparison.revision_rounds}`",
            f"- verifier_status: `{comparison.verifier_status or '-'}`",
            "",
            "### Reviewer Findings",
            "",
            *(f"- {finding}" for finding in comparison.reviewer_findings or ["None recorded."]),
            "",
            "## Failure Taxonomy",
            "",
            f"- taxonomy: `{comparison.failure_taxonomy or 'unclassified'}`",
            f"- single_status: `{comparison.single_status or '-'}`",
            f"- multi_status: `{comparison.multi_status or '-'}`",
            "",
            "## Failure Lens",
            "",
            "This section turns one failed or blocked run into concrete follow-up work. It is intentionally more useful than a binary pass/fail badge.",
            "",
            "| Lens | Evidence to inspect | Interview talking point |",
            "| --- | --- | --- |",
            *_failure_lens_rows(comparison),
            "",
            "## Recommendation",
            "",
            comparison.recommendation,
            "",
        ]
    )


def write_evaluation_artifacts(comparison: EvaluationComparison, output_dir: str | Path) -> tuple[Path, Path]:
    """Write comparison.json and evaluation_report.md."""

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "comparison.json"
    report_path = output / "evaluation_report.md"
    json_path.write_text(json.dumps(comparison.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(render_evaluation_report(comparison), encoding="utf-8")
    return json_path, report_path


def _quality_signal(comparison: EvaluationComparison) -> str:
    """Summarize quality without overstating benchmark success."""

    if comparison.multi_patch_generated and not comparison.single_patch_generated:
        return "multi-agent produced a patch where single-agent did not"
    if comparison.single_patch_generated and not comparison.multi_patch_generated:
        return "single-agent produced a patch where multi-agent did not"
    if comparison.multi_patch_generated and comparison.single_patch_generated:
        return "both modes produced patches; compare official eval or reviewer/verifier evidence"
    return "neither mode produced a patch"


def _failure_lens_rows(comparison: EvaluationComparison) -> list[str]:
    """Map comparison metrics to debugging lenses for the report."""

    rows = [
        "| Model / Provider | LLM status, token usage, cost, retry/fallback errors | Provider instability is separated from agent logic failure. |",
        "| Context | selected files, context section sizes, truncation/compaction events | If the right file is absent, improve retrieval before changing prompts. |",
        "| Tool / Runtime | tool calls, failed tool calls, command policy decisions | Tool failures are actionable runtime bugs or policy gaps, not vague model badness. |",
        "| Safety / Policy | blocked commands, approval decisions, protected paths | High-risk actions are enforced by code, not trusted to the prompt. |",
        "| Evaluation | patch generated, official eval status, reviewer/verifier decision | A patch is not a resolved-rate claim until official evaluation or focused validation passes. |",
    ]
    if comparison.reviewer_findings:
        rows.append(
            f"| Reviewer | {'; '.join(comparison.reviewer_findings[:3])} | Reviewer findings become targeted revision prompts instead of informal feedback. |"
        )
    if comparison.failure_taxonomy:
        rows.append(
            f"| Taxonomy | `{comparison.failure_taxonomy}` | Failure labels make badcases searchable and comparable across runs. |"
        )
    return rows

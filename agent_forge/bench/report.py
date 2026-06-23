from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .types import BenchRunSummary


def write_bench_artifacts(summary: BenchRunSummary) -> tuple[Path, Path]:
    """Write ``results.json`` and the human result card.

    The result card is the artifact a reviewer should read first. It separates
    three ideas that are often confused:
        generated patch: the agent produced a diff;
        official evaluation: SWE-bench Docker harness judged the patch;
        runtime evidence: trace/usage explain how the patch was produced.
    """

    summary.output_dir.mkdir(parents=True, exist_ok=True)
    results_json = summary.output_dir / "results.json"
    report_md = summary.output_dir / "report.md"
    results_json.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    report_md.write_text(render_bench_report(summary), encoding="utf-8")
    return results_json, report_md


def render_bench_report(summary: BenchRunSummary) -> str:
    """Render a concise SWE-bench result card."""

    status_counts = Counter(result.status for result in summary.case_results)
    eval_counts = Counter(result.evaluation_status for result in summary.case_results)
    patch_generated = sum(1 for result in summary.case_results if result.patch_chars > 0)
    total = len(summary.case_results)

    lines = [
        "# Agent Forge Benchmark Result",
        "",
        "## Run",
        "",
        f"- run_id: `{summary.run_id}`",
        f"- dataset: `{summary.dataset_name}`",
        f"- split: `{summary.split}`",
        f"- provider/model: `{summary.provider}` / `{summary.model or 'default'}`",
        f"- cases: `{total}`",
        f"- predictions: `{summary.predictions_path}`",
    ]
    if summary.baseline_predictions_path:
        lines.append(f"- direct baseline predictions: `{summary.baseline_predictions_path}`")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- patches generated: `{patch_generated}/{total}`",
            f"- agent statuses: `{dict(status_counts)}`",
            f"- official evaluation statuses: `{dict(eval_counts)}`",
        ]
    )
    if summary.official_eval_command:
        lines.extend(
            [
                "",
                "## Official SWE-bench Evaluation",
                "",
                "Command:",
                "",
                "```bash",
                " ".join(summary.official_eval_command),
                "```",
                "",
                f"- exit_code: `{summary.official_eval_exit_code}`",
            ]
        )
        if summary.official_eval_output:
            lines.extend(
                [
                    "",
                    "Last output:",
                    "",
                    "```text",
                    summary.official_eval_output[-4000:],
                    "```",
                ]
            )
    else:
        lines.extend(
            [
                "",
                "## Official SWE-bench Evaluation",
                "",
                "Not run in this command. The generated `predictions.jsonl` is compatible with the SWE-bench harness.",
            ]
        )

    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| instance | repo | status | eval | patch chars | trace | usage |",
            "| --- | --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for result in summary.case_results:
        usage = result.usage_report_path or ""
        lines.append(
            "| "
            f"`{result.instance_id}` | `{result.repo}` | `{result.status}` | "
            f"`{result.evaluation_status}` | {result.patch_chars} | "
            f"[trace]({result.trace_path}) | [usage]({usage}) |"
        )

    lines.extend(
        [
            "",
            "## Failure Taxonomy",
            "",
            "Use the case rows above as the first triage layer:",
            "",
            "- `patch_generated`: the agent produced a diff; run official evaluation before claiming resolved.",
            "- `no_patch`: the loop ended without a diff, usually context/tool/step-budget failure.",
            "- `blocked`: guardrail, provider config, command policy, or runtime budget stopped the case.",
            "- `official_eval_failed`: SWE-bench harness ran and rejected the patch.",
            "",
            "## Failure Diagnosis",
            "",
            "| instance | class | diagnosis | next action | evidence |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for result in summary.case_results:
        next_action = result.next_actions[0] if result.next_actions else ""
        evidence = "; ".join(result.diagnosis_evidence[:4])
        lines.append(
            "| "
            f"`{result.instance_id}` | `{result.failure_class or 'unclassified'}` | "
            f"{_table_cell(result.diagnosis)} | {_table_cell(next_action)} | {_table_cell(evidence)} |"
        )
    lines.extend(
        [
            "",
            "Use this table as the first iteration target. Repeated-tool failures point to loop recovery, context misses point to retrieval/ranking, and official-eval failures point to patch correctness.",
            "",
            "## Notes",
            "",
        ]
    )
    if summary.notes:
        lines.extend(f"- {note}" for note in summary.notes)
    else:
        lines.append("- No additional notes.")
    lines.append("")
    return "\n".join(lines)


def _table_cell(value: str) -> str:
    """Keep generated Markdown tables readable."""

    return (value or "").replace("|", "\\|").replace("\n", " ").strip()

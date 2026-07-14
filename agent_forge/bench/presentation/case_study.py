from __future__ import annotations

from pathlib import Path

from agent_forge.bench.domain.models import BenchCaseResult


def render_case_study(result: BenchCaseResult) -> str:
    evidence = result.diagnosis_evidence or []
    next_actions = result.next_actions or []
    lines = [
        f"# Case Study: {result.instance_id}",
        "",
        "## Why this case matters",
        "",
        f"This case exercises `{result.failure_class or 'unclassified'}` in a real repository task.",
        "",
        "## Runtime Outcome",
        "",
        f"- repo: `{result.repo}`",
        f"- status: `{result.status}`",
        f"- evaluation_status: `{result.evaluation_status}`",
        f"- local_validation_status: `{result.local_validation_status}`",
        f"- official_evaluation_status: `{result.official_evaluation_status}`",
        f"- official_evaluation_report: `{result.official_evaluation_report_path or '-'}`",
        f"- patch_chars: `{result.patch_chars}`",
        f"- trace: `{result.trace_path}`",
        f"- usage: `{result.usage_report_path or '-'}`",
        f"- patch: `{result.patch_path}`",
        "",
        "## Failure Classification",
        "",
        f"- class: `{result.failure_class or 'unclassified'}`",
        f"- diagnosis: {result.diagnosis or 'No diagnosis recorded.'}",
        "",
        "## Evidence",
        "",
    ]
    lines.extend(f"- {item}" for item in evidence[:8])
    if not evidence:
        lines.append("- No structured evidence recorded.")
    lines.extend(["", "## Next Actions", ""])
    lines.extend(f"- {item}" for item in next_actions[:5])
    if not next_actions:
        lines.append("- Inspect trace and promote repeated patterns into taxonomy rules.")
    lines.extend(
        [
            "",
            "## Runtime Lesson",
            "",
            "Use this case to decide whether the next improvement belongs in context selection, tool governance, sandbox policy, diagnostics, or model prompting. Do not treat a candidate patch as official resolution without evaluation evidence.",
            "",
        ]
    )
    return "\n".join(lines)


# PRIMARY ENTRYPOINT: write the human-readable evidence narrative for one final case.
def write_case_study(result: BenchCaseResult) -> Path:
    """Write ``case_study.md`` from a fully evaluated and diagnosed case result."""

    path = Path(result.patch_path).parent / "case_study.md"
    path.write_text(render_case_study(result), encoding="utf-8")
    return path

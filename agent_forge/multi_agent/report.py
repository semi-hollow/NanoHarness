from __future__ import annotations

from collections import Counter

from .types import MultiAgentRunSummary


def render_multi_agent_report(summary: MultiAgentRunSummary) -> str:
    """Render a human-readable coordinator report."""

    role_statuses = Counter(result.status for result in summary.role_results)
    decisions = Counter(result.decision for result in summary.role_results if result.decision)
    lines = [
        "# Multi-Agent Run Report",
        "",
        "## Summary",
        "",
        f"- run_id: `{summary.run_id}`",
        f"- profile: `{summary.profile}`",
        f"- status: `{summary.status}`",
        f"- revision_rounds: `{summary.revision_rounds}`",
        f"- role_statuses: `{dict(role_statuses)}`",
        f"- decisions: `{dict(decisions)}`",
        "",
        "## Task",
        "",
        summary.task,
        "",
        "## Role Runs",
        "",
        "| round | role | status | decision | artifacts |",
        "| ---: | --- | --- | --- | --- |",
    ]
    for result in summary.role_results:
        artifacts = ", ".join(f"`{artifact_id}`" for artifact_id in result.artifact_ids)
        lines.append(
            f"| {result.round_index} | `{result.role}` | `{result.status}` | "
            f"`{result.decision or '-'}` | {artifacts or '-'} |"
        )

    lines.extend(["", "## Artifacts", ""])
    for artifact in summary.artifacts:
        lines.append(f"- `{artifact.id}` ({artifact.role}/{artifact.kind}, round {artifact.round_index}): {artifact.path}")
        if artifact.summary:
            lines.append(f"  - {artifact.summary}")

    lines.extend(["", "## Final Answer", "", summary.final_answer or "(empty)", ""])
    return "\n".join(lines)

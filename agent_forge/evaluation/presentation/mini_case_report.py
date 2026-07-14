from __future__ import annotations

from agent_forge.evaluation.domain.mini_cases import MiniAgentCase, MiniCaseEvaluation


def render_mini_case_report(
    case: MiniAgentCase,
    result: MiniCaseEvaluation,
) -> str:
    lines = [
        "# Mini Case Evaluation",
        "",
        "This is not a benchmark leaderboard. It is a small deterministic scorecard for explicit evidence.",
        "",
        f"- case_id: `{case.case_id}`",
        f"- domain: `{case.domain}`",
        f"- status: `{result.status}`",
        f"- task: {case.task}",
        "",
        "## Expected Artifacts",
        "",
    ]
    lines.extend(f"- `{artifact}`" for artifact in case.expected_artifacts)
    lines.extend(["", "## Dimension Scores", ""])
    for dimension, score in result.dimension_scores.items():
        lines.append(
            f"- `{dimension}`: `{score['status']}` value=`{score.get('value')}`"
        )
    lines.extend(["", "## Safety Notes", ""])
    lines.extend(f"- {note}" for note in case.safety_notes)
    lines.append("")
    return "\n".join(lines)

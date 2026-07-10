from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MiniAgentCase:
    """A small non-benchmark scenario for general Agent application interviews."""

    case_id: str
    domain: str
    task: str
    tools: list[str]
    expected_artifacts: list[str]
    eval_dimensions: list[str]
    safety_notes: list[str]


@dataclass(frozen=True)
class MiniCaseEvaluation:
    """Deterministic scorecard for one small Agent application case."""

    case_id: str
    status: str
    dimension_scores: dict[str, dict[str, Any]]
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "status": self.status,
            "dimension_scores": self.dimension_scores,
            "evidence": self.evidence,
        }


def load_mini_cases(root: str | Path | None = None) -> list[MiniAgentCase]:
    """Load small non-coding cases used to explain general Agent evaluation."""

    case_root = Path(root) if root is not None else _default_case_root()
    cases: list[MiniAgentCase] = []
    for path in sorted(case_root.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        cases.append(
            MiniAgentCase(
                case_id=str(data["case_id"]),
                domain=str(data["domain"]),
                task=str(data["task"]),
                tools=list(data.get("tools") or []),
                expected_artifacts=list(data.get("expected_artifacts") or []),
                eval_dimensions=list(data.get("eval_dimensions") or []),
                safety_notes=list(data.get("safety_notes") or []),
            )
        )
    return cases


def evaluate_mini_case(case: MiniAgentCase, evidence: dict[str, Any] | None = None) -> MiniCaseEvaluation:
    """Score one mini case from explicit evidence instead of model judgment."""

    evidence = evidence or {}
    scores = {
        dimension: _score_dimension(case, dimension, evidence)
        for dimension in case.eval_dimensions
    }
    if not evidence:
        status = "needs_evidence"
    elif all(score["status"] == "passed" for score in scores.values()):
        status = "passed"
    else:
        status = "failed"
    return MiniCaseEvaluation(
        case_id=case.case_id,
        status=status,
        dimension_scores=scores,
        evidence=evidence,
    )


def write_mini_case_report(
    case: MiniAgentCase,
    result: MiniCaseEvaluation,
    output_dir: str | Path,
) -> Path:
    """Write a compact report for one mini case evaluation."""

    case_dir = Path(output_dir) / case.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "mini_case_result.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    report_path = case_dir / "mini_case_report.md"
    report_path.write_text(_render_mini_case_report(case, result), encoding="utf-8")
    return report_path


def run_mini_cases(
    *,
    case_id: str = "all",
    evidence: dict[str, Any] | None = None,
    output_dir: str | Path = ".agent_forge/mini_cases",
    case_root: str | Path | None = None,
) -> list[Path]:
    """Evaluate one or all mini cases and write report artifacts."""

    cases = load_mini_cases(case_root)
    if case_id != "all":
        cases = [case for case in cases if case.case_id == case_id]
    if not cases:
        raise ValueError(f"mini case not found: {case_id}")

    evidence = evidence or {}
    report_paths: list[Path] = []
    for case in cases:
        case_evidence = evidence.get(case.case_id, evidence) if isinstance(evidence, dict) else {}
        result = evaluate_mini_case(case, case_evidence)
        report_paths.append(write_mini_case_report(case, result, output_dir))
    return report_paths


def _default_case_root() -> Path:
    return Path(__file__).resolve().parents[2] / "docs" / "evaluation" / "mini-cases"


def _score_dimension(case: MiniAgentCase, dimension: str, evidence: dict[str, Any]) -> dict[str, Any]:
    artifacts = set(evidence.get("artifacts") or [])
    citations = list(evidence.get("citations") or [])
    unsupported_claim_count = _int_value(evidence.get("unsupported_claim_count"), default=0)
    safety_violation = bool(evidence.get("safety_violation", False))
    tool_calls = _int_value(evidence.get("tool_calls"), default=-1)
    human_intervention_count = _int_value(evidence.get("human_intervention_count"), default=0)

    if dimension == "task_success":
        missing = [artifact for artifact in case.expected_artifacts if artifact not in artifacts]
        return {
            "status": "passed" if not missing else "failed",
            "value": sorted(artifacts),
            "expected": case.expected_artifacts,
            "missing": missing,
        }
    if dimension == "evidence_quality":
        passed = bool(citations) and unsupported_claim_count == 0
        return {
            "status": "passed" if passed else "failed",
            "value": {"citation_count": len(citations), "unsupported_claim_count": unsupported_claim_count},
        }
    if dimension == "source_coverage":
        required = _int_value(evidence.get("required_source_count"), default=1)
        return {
            "status": "passed" if len(citations) >= required else "failed",
            "value": len(citations),
            "expected_min": required,
        }
    if dimension == "unsupported_claim_count":
        return {
            "status": "passed" if unsupported_claim_count == 0 else "failed",
            "value": unsupported_claim_count,
        }
    if dimension == "tool_efficiency":
        max_tool_calls = _int_value(evidence.get("max_tool_calls"), default=8)
        known = tool_calls >= 0
        return {
            "status": "passed" if known and tool_calls <= max_tool_calls else "failed",
            "value": tool_calls if known else "missing",
            "expected_max": max_tool_calls,
        }
    if dimension == "safety_violation":
        return {
            "status": "passed" if not safety_violation else "failed",
            "value": safety_violation,
        }
    if dimension == "policy_compliance":
        passed = not safety_violation and human_intervention_count > 0
        return {
            "status": "passed" if passed else "failed",
            "value": {
                "safety_violation": safety_violation,
                "human_intervention_count": human_intervention_count,
            },
        }
    if dimension == "human_intervention_count":
        return {
            "status": "passed" if human_intervention_count > 0 else "failed",
            "value": human_intervention_count,
            "expected_min": 1,
        }
    if dimension == "recovery_success":
        recovery_success = bool(evidence.get("recovery_success", False))
        return {
            "status": "passed" if recovery_success else "failed",
            "value": recovery_success,
        }
    return {
        "status": "not_scored",
        "value": evidence.get(dimension),
    }


def _render_mini_case_report(case: MiniAgentCase, result: MiniCaseEvaluation) -> str:
    lines = [
        "# Mini Case Evaluation",
        "",
        "This is not a benchmark leaderboard. It is a small deterministic scorecard for interview discussion.",
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
        lines.append(f"- `{dimension}`: `{score['status']}` value=`{score.get('value')}`")
    lines.extend(["", "## Safety Notes", ""])
    lines.extend(f"- {note}" for note in case.safety_notes)
    lines.append("")
    return "\n".join(lines)


def _int_value(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

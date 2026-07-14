from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_forge.evaluation.domain.mini_cases import MiniAgentCase, MiniCaseEvaluation


def load_mini_cases(root: str | Path | None = None) -> list[MiniAgentCase]:
    """Parse mini-case definitions from JSON files."""

    case_root = Path(root) if root is not None else _default_case_root()
    cases: list[MiniAgentCase] = []
    for path in sorted(case_root.glob("*.json")):
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
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


def write_mini_case_artifacts(
    case: MiniAgentCase,
    result: MiniCaseEvaluation,
    output_dir: str | Path,
    report: str,
) -> Path:
    """Persist the result DTO and its already-rendered report."""

    case_dir = Path(output_dir) / case.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "mini_case_result.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    report_path = case_dir / "mini_case_report.md"
    report_path.write_text(report, encoding="utf-8")
    return report_path


def _default_case_root() -> Path:
    return Path(__file__).resolve().parents[3] / "docs" / "evaluation" / "mini-cases"

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


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


def _default_case_root() -> Path:
    return Path(__file__).resolve().parents[2] / "docs" / "evaluation" / "mini-cases"

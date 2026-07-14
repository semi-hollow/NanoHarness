from __future__ import annotations

from typing import Any

from agent_forge.evaluation.domain.mini_cases import (
    MiniAgentCase,
    MiniCaseEvaluation,
    evaluate_mini_case,
)


# PRIMARY ENTRYPOINT: evaluate selected cases without filesystem side effects.
def evaluate_selected_cases(
    cases: list[MiniAgentCase],
    *,
    case_id: str = "all",
    evidence: dict[str, Any] | None = None,
) -> list[tuple[MiniAgentCase, MiniCaseEvaluation]]:
    """Select one or all cases and return deterministic evaluations."""

    selected = cases if case_id == "all" else [case for case in cases if case.case_id == case_id]
    if not selected:
        raise ValueError(f"mini case not found: {case_id}")

    evidence = evidence or {}
    results: list[tuple[MiniAgentCase, MiniCaseEvaluation]] = []
    for case in selected:
        case_evidence = evidence.get(case.case_id, evidence)
        normalized = case_evidence if isinstance(case_evidence, dict) else {}
        results.append((case, evaluate_mini_case(case, normalized)))
    return results

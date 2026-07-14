from __future__ import annotations

from typing import Any

from agent_forge.evaluation.domain.mini_cases import (
    MiniAgentCase,
    MiniCaseEvaluation,
    evaluate_mini_case,
)

# 主要入口：下方定义承接该模块的核心调用。
def evaluate_selected_cases(
    cases: list[MiniAgentCase],
    *,
    case_id: str = "all",
    evidence: dict[str, Any] | None = None,
) -> list[tuple[MiniAgentCase, MiniCaseEvaluation]]:
    """筛选 mini-case，并用确定性证据规则逐项评估。"""

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

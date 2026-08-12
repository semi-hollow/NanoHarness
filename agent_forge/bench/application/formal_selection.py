"""对已验证正式实验执行纯函数、失败关闭的胜者聚合。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence, TypedDict

from agent_forge.bench.application.formal_campaign import FormalCampaignRecord


@dataclass(frozen=True)
class ExpectedFormalSlot:
    """冻结单个正式槽位的候选与 Case 身份。"""

    slot_id: str
    candidate_id: str
    case_id: str


class CandidateSelectionSummary(TypedDict):
    candidate_id: str
    denominator: int
    resolved: int
    decided: int
    empty: int
    infrastructure: int
    failed_tools: int
    selection_key: list[int]
    run_evidence: list[dict[str, object]]


class FormalSelectionSummary(TypedDict):
    schema_version: int
    status: Literal["winner_selected", "invalid_no_winner"]
    winner: str | None
    candidate_order: list[str]
    observed_records: int
    candidates: list[CandidateSelectionSummary]
    reason_codes: list[str]


def aggregate_formal_winner(
    records: Sequence[FormalCampaignRecord],
    expected_slots: Sequence[ExpectedFormalSlot],
    candidate_order: Sequence[str],
) -> FormalSelectionSummary:
    """仅从映射完整、基础设施干净的二十槽实验中选择胜者。"""

    reasons = _plan_reasons(expected_slots, candidate_order)
    if len(records) != 20:
        reasons.append("record_count_drift")
    if [item.slot_id for item in records] != [item.slot_id for item in expected_slots]:
        reasons.append("slot_order_drift")
    if reasons:
        return _no_winner(records, candidate_order, reasons)

    run_ids: set[str] = set()
    by_candidate: dict[str, CandidateSelectionSummary] = {
        candidate: _empty_candidate(candidate) for candidate in candidate_order
    }
    for record, slot in zip(records, expected_slots, strict=True):
        run = record.validated
        if record.status != "validated" or record.return_code != 0 or run is None:
            reasons.append("non_validated_record")
            continue
        if run.planned != 1 or run.finalized != 1:
            reasons.append("incomplete_formal_run")
        if run.infrastructure != 0:
            reasons.append("infrastructure_failure")
        if run.transport_retries != 0:
            reasons.append("transport_retry_observed")
        if (
            run.resolved not in {0, 1}
            or run.unresolved not in {0, 1}
            or run.decided != run.resolved + run.unresolved
            or run.empty not in {0, 1}
            or run.decided + run.empty + run.infrastructure != 1
            or run.failed_tools < 0
        ):
            reasons.append("invalid_run_metrics")
        if not run.run_id or run.run_id in run_ids:
            reasons.append("duplicate_or_missing_run_id")
        run_ids.add(run.run_id)
        try:
            evidence = run.evidence(slot.slot_id)
        except (KeyError, TypeError, ValueError):
            reasons.append("invalid_run_evidence")
            continue
        candidate = by_candidate[slot.candidate_id]
        candidate["resolved"] += run.resolved
        candidate["decided"] += run.decided
        candidate["empty"] += run.empty
        candidate["infrastructure"] += run.infrastructure
        candidate["failed_tools"] += run.failed_tools
        candidate["run_evidence"].append({"case_id": slot.case_id, **evidence})

    candidates = [by_candidate[candidate] for candidate in candidate_order]
    if any(len(item["run_evidence"]) != 10 for item in candidates):
        reasons.append("candidate_denominator_incomplete")
    if reasons:
        return _no_winner(records, candidate_order, reasons)
    for index, candidate in enumerate(candidates):
        candidate["selection_key"] = [
            -candidate["resolved"],
            -candidate["decided"],
            candidate["empty"],
            candidate["infrastructure"],
            candidate["failed_tools"],
            index,
        ]
    winner = min(candidates, key=lambda item: tuple(item["selection_key"]))
    return {
        "schema_version": 1,
        "status": "winner_selected",
        "winner": winner["candidate_id"],
        "candidate_order": list(candidate_order),
        "observed_records": 20,
        "candidates": candidates,
        "reason_codes": [],
    }


def _plan_reasons(
    slots: Sequence[ExpectedFormalSlot], candidate_order: Sequence[str]
) -> list[str]:
    reasons: list[str] = []
    if len(candidate_order) != 2 or len(set(candidate_order)) != 2:
        reasons.append("invalid_candidate_order")
        return reasons
    if len(slots) != 20 or [item.slot_id for item in slots] != [
        f"slot-{index:03d}" for index in range(1, 21)
    ]:
        reasons.append("invalid_expected_slot_order")
        return reasons
    counts = {candidate: 0 for candidate in candidate_order}
    cases: dict[str, set[str]] = {}
    for slot in slots:
        if slot.candidate_id not in counts or not slot.case_id:
            reasons.append("invalid_expected_mapping")
            return reasons
        counts[slot.candidate_id] += 1
        cases.setdefault(slot.case_id, set()).add(slot.candidate_id)
    if (
        set(counts.values()) != {10}
        or len(cases) != 10
        or any(candidates != set(candidate_order) for candidates in cases.values())
    ):
        reasons.append("invalid_expected_mapping")
    return reasons


def _empty_candidate(candidate_id: str) -> CandidateSelectionSummary:
    return {
        "candidate_id": candidate_id,
        "denominator": 10,
        "resolved": 0,
        "decided": 0,
        "empty": 0,
        "infrastructure": 0,
        "failed_tools": 0,
        "selection_key": [],
        "run_evidence": [],
    }


def _no_winner(
    records: Sequence[FormalCampaignRecord],
    candidate_order: Sequence[str],
    reasons: Sequence[str],
) -> FormalSelectionSummary:
    return {
        "schema_version": 1,
        "status": "invalid_no_winner",
        "winner": None,
        "candidate_order": list(candidate_order),
        "observed_records": len(records),
        "candidates": [],
        "reason_codes": list(dict.fromkeys(reasons)),
    }

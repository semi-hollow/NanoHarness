from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from typing import Callable, Sequence, cast

import pytest

from agent_forge.bench.application.formal_campaign import FormalCampaignRecord
from agent_forge.bench.application.formal_selection import (
    ExpectedFormalSlot,
    aggregate_formal_winner,
)
from agent_forge.bench.formal_artifacts import ValidatedFormalRun


CANDIDATES = ("v4-pro", "glm")


def _slots() -> tuple[ExpectedFormalSlot, ...]:
    slots: list[ExpectedFormalSlot] = []
    for case_index in range(1, 11):
        order = CANDIDATES if case_index % 2 else tuple(reversed(CANDIDATES))
        for candidate in order:
            slots.append(
                ExpectedFormalSlot(
                    f"slot-{len(slots) + 1:03d}",
                    candidate,
                    f"project__repo-{case_index}",
                )
            )
    return tuple(slots)


def _validated(
    index: int,
    *,
    resolved: int = 0,
    decided: int = 1,
    empty: int = 0,
    infrastructure: int = 0,
    failed_tools: int = 0,
) -> ValidatedFormalRun:
    unresolved = decided - resolved
    digest = f"{index + 1:064x}"
    return ValidatedFormalRun(
        run_id=f"formal-run-{index + 1:03d}",
        planned=1,
        finalized=1,
        resolved=resolved,
        unresolved=unresolved,
        decided=decided,
        empty=empty,
        infrastructure=infrastructure,
        failed_tools=failed_tools,
        tokens=100,
        cost=Decimal("0"),
        patch_binding_parts=(),
        artifact_sha256={
            "results.json": digest,
            "scorecard.json": digest,
            "predictions.jsonl": digest,
            "official_aggregate.json": digest,
        },
        command_argv_sha256=digest,
        expected_source_identity_sha256=digest,
        frozen_inputs_sha256=digest,
        config_sha256=digest,
    )


def _records(
    outcomes: dict[str, list[dict[str, int]]] | None = None,
) -> tuple[FormalCampaignRecord, ...]:
    outcomes = outcomes or {}
    offsets = {candidate: 0 for candidate in CANDIDATES}
    records: list[FormalCampaignRecord] = []
    for index, slot in enumerate(_slots()):
        candidate_outcomes = outcomes.get(slot.candidate_id, [])
        offset = offsets[slot.candidate_id]
        values = candidate_outcomes[offset] if offset < len(candidate_outcomes) else {}
        offsets[slot.candidate_id] += 1
        records.append(
            FormalCampaignRecord(
                slot.slot_id,
                "validated",
                0,
                _validated(index, **values),
            )
        )
    return tuple(records)


def _outcomes(
    *, resolved: int, empty: int = 0, failed_tools: int = 0
) -> list[dict[str, int]]:
    values: list[dict[str, int]] = []
    for index in range(10):
        if index < resolved:
            values.append({"resolved": 1})
        elif index < resolved + empty:
            values.append({"decided": 0, "empty": 1})
        else:
            values.append({"failed_tools": failed_tools if index == 9 else 0})
    return values


def test_complete_campaign_selects_winner_and_emits_only_json_safe_evidence() -> None:
    records = _records(
        {
            "v4-pro": _outcomes(resolved=7),
            "glm": _outcomes(resolved=6),
        }
    )

    summary = aggregate_formal_winner(records, _slots(), CANDIDATES)

    assert summary["status"] == "winner_selected"
    assert summary["winner"] == "v4-pro"
    assert [item["denominator"] for item in summary["candidates"]] == [10, 10]
    first_evidence = summary["candidates"][0]["run_evidence"][0]
    assert first_evidence["case_id"] == "project__repo-1"
    assert first_evidence["shard"] == "slot-001"
    assert first_evidence["results_sha256"] == f"{1:064x}"
    json.dumps(summary, sort_keys=True)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (_outcomes(resolved=6), _outcomes(resolved=5)),
        (_outcomes(resolved=5), _outcomes(resolved=5, empty=1)),
        (_outcomes(resolved=5, empty=0), _outcomes(resolved=5, empty=1)),
        (
            _outcomes(resolved=5, failed_tools=0),
            _outcomes(resolved=5, failed_tools=2),
        ),
    ],
)
def test_frozen_lexicographic_metric_prefers_first(
    first: list[dict[str, int]], second: list[dict[str, int]]
) -> None:
    summary = aggregate_formal_winner(
        _records({"v4-pro": first, "glm": second}), _slots(), CANDIDATES
    )

    assert summary["winner"] == "v4-pro"


def test_candidate_order_is_final_tie_break() -> None:
    records = _records()

    normal = aggregate_formal_winner(records, _slots(), CANDIDATES)
    reversed_order = aggregate_formal_winner(
        records, _slots(), tuple(reversed(CANDIDATES))
    )

    assert normal["winner"] == "v4-pro"
    assert reversed_order["winner"] == "glm"


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda records: records[:-1], "record_count_drift"),
        (
            lambda records: (records[1], records[0], *records[2:]),
            "slot_order_drift",
        ),
        (
            lambda records: (
                replace(records[0], status="invalid", validated=None),
                *records[1:],
            ),
            "non_validated_record",
        ),
        (
            lambda records: (
                replace(
                    records[0],
                    validated=replace(
                        cast(ValidatedFormalRun, records[0].validated),
                        infrastructure=1,
                    ),
                ),
                *records[1:],
            ),
            "infrastructure_failure",
        ),
        (
            lambda records: (
                replace(
                    records[0],
                    validated=replace(
                        cast(ValidatedFormalRun, records[0].validated), finalized=0
                    ),
                ),
                *records[1:],
            ),
            "incomplete_formal_run",
        ),
        (
            lambda records: (
                replace(
                    records[0],
                    validated=replace(
                        cast(ValidatedFormalRun, records[0].validated),
                        transport_retries=1,
                    ),
                ),
                *records[1:],
            ),
            "transport_retry_observed",
        ),
        (
            lambda records: (
                records[0],
                replace(
                    records[1],
                    validated=replace(
                        cast(ValidatedFormalRun, records[1].validated),
                        run_id=cast(ValidatedFormalRun, records[0].validated).run_id,
                    ),
                ),
                *records[2:],
            ),
            "duplicate_or_missing_run_id",
        ),
    ],
)
def test_any_incomplete_or_invalid_record_returns_no_winner(
    mutate: Callable[
        [tuple[FormalCampaignRecord, ...]], Sequence[FormalCampaignRecord]
    ],
    reason: str,
) -> None:
    summary = aggregate_formal_winner(mutate(_records()), _slots(), CANDIDATES)

    assert summary["status"] == "invalid_no_winner"
    assert summary["winner"] is None
    assert summary["candidates"] == []
    assert reason in summary["reason_codes"]


def test_invalid_expected_candidate_case_mapping_returns_no_winner() -> None:
    slots = list(_slots())
    slots[-1] = replace(slots[-1], candidate_id=slots[-2].candidate_id)

    summary = aggregate_formal_winner(_records(), slots, CANDIDATES)

    assert summary["status"] == "invalid_no_winner"
    assert summary["winner"] is None
    assert "invalid_expected_mapping" in summary["reason_codes"]

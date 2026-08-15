from __future__ import annotations

import hashlib

import pytest

from agent_forge.bench.domain.evaluation_contract import (
    EXPECTED_OUTCOMES,
    EvaluationContract,
)


def _contract(**overrides: object) -> EvaluationContract:
    case_ids = ("repo__case-1", "repo__case-2")
    values = {
        "experiment_id": "tool-ab",
        "comparison": "matched_pass_at_1",
        "primary_metric": "official_resolved / planned",
        "case_ids": case_ids,
        "ordered_case_ids_sha256": hashlib.sha256(
            "\n".join(case_ids).encode()
        ).hexdigest(),
        "shard_size": 1,
        "benchmark_args": ("--model", "quality-model", "--evaluate"),
        "variant_sources": (
            ("control", "0" * 40),
            ("treatment", "1" * 40),
        ),
        "correctness_reruns": 0,
        "terminal_outcomes": EXPECTED_OUTCOMES,
        "analysis_in_pipeline": False,
    }
    values.update(overrides)
    return EvaluationContract(**values)  # type: ignore[arg-type]


def test_contract_keeps_comparison_matched_and_sharding_deterministic() -> None:
    contract = _contract()

    assert contract.shards == (("repo__case-1",), ("repo__case-2",))
    assert contract.source_for("treatment") == "1" * 40


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("correctness_reruns", 1, "reruns"),
        ("analysis_in_pipeline", True, "interpretation"),
        ("primary_metric", "resolved / completed", "planned denominator"),
        ("terminal_outcomes", ("resolved", "unresolved"), "terminal outcomes"),
        ("benchmark_args", ("--api-key", "secret", "--evaluate"), "secret flags"),
    ],
)
def test_contract_rejects_invalid_evaluation_design(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _contract(**{field: value})

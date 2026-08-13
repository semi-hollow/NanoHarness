from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.summarize_tool_aci_golden_20 import (
    ReportRefused,
    _decision,
    _git_blob_sha256,
    _mcnemar_exact,
    _portable_path,
    _safe_aggregate,
    _wilson,
)


def test_wilson_interval_and_exact_mcnemar_match_frozen_result() -> None:
    assert _wilson(14, 20) == [0.481027, 0.854523]
    assert _wilson(13, 20) == [0.432854, 0.818808]
    assert _mcnemar_exact(gains=1, regressions=2) == 1.0


def test_decision_rejects_any_regression_or_negative_net() -> None:
    assert _decision(14, 13, gains=1, regressions=2, activated=True) == "reject"
    assert (
        _decision(10, 11, gains=2, regressions=1, activated=True) == "directional_only"
    )
    assert (
        _decision(10, 13, gains=3, regressions=0, activated=True) == "strong_positive"
    )


def test_safe_aggregate_requires_complete_non_infra_denominator(tmp_path: Path) -> None:
    run_id = "run-1"
    path = tmp_path / f"agent.{run_id}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "total_instances": 2,
                "submitted_instances": 2,
                "completed_instances": 2,
                "resolved_instances": 1,
                "unresolved_instances": 1,
                "empty_patch_instances": 0,
                "error_instances": 0,
                "resolved_ids": ["case-a"],
                "unresolved_ids": ["case-b"],
                "empty_patch_ids": [],
                "error_ids": [],
                "incomplete_ids": [],
            }
        ),
        encoding="utf-8",
    )

    aggregate_path, outcomes = _safe_aggregate(tmp_path, run_id, ["case-a", "case-b"])

    assert aggregate_path == path
    assert outcomes == {"case-a": "resolved", "case-b": "unresolved"}


def test_safe_aggregate_rejects_infrastructure_outcome(tmp_path: Path) -> None:
    run_id = "run-2"
    (tmp_path / f"agent.{run_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "total_instances": 1,
                "submitted_instances": 1,
                "completed_instances": 1,
                "resolved_instances": 0,
                "unresolved_instances": 0,
                "empty_patch_instances": 0,
                "error_instances": 1,
                "resolved_ids": [],
                "unresolved_ids": [],
                "empty_patch_ids": [],
                "error_ids": ["case-a"],
                "incomplete_ids": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReportRefused, match="infrastructure outcome"):
        _safe_aggregate(tmp_path, run_id, ["case-a"])


def test_portable_path_hides_local_workspace_prefix(tmp_path: Path) -> None:
    artifact = tmp_path / "evidence" / "result.json"
    assert _portable_path(tmp_path, artifact) == "evidence/result.json"


def test_frozen_treatment_blob_survives_stable_branch_rollback() -> None:
    project_root = Path(__file__).resolve().parents[1]
    assert (
        _git_blob_sha256(
            project_root,
            "296000864d6a2c1476c28b790f030b0ffc4cca5b",
            "agent_forge/tools/find_files.py",
        )
        == "4ae65f3d3df79a551d8736cdcfcf663107b7014470a4e7298b7ad6fd2075179e"
    )

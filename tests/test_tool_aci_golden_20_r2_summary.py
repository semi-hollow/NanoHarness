from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.summarize_tool_aci_golden_20_r2 import (
    RUN_SOURCE_COMMIT,
    RUN_SOURCE_TAG,
    ReportRefused,
    _changed_files,
    _decision,
    _git_blob_sha256,
    _git_revision,
    _mcnemar_exact,
    _safe_aggregate,
    _wilson,
)


def test_frozen_statistical_helpers() -> None:
    assert _wilson(14, 20) == [0.481027, 0.854523]
    assert _mcnemar_exact(gains=1, regressions=2) == 1.0


@pytest.mark.parametrize(
    ("resolved", "regressions", "activated", "expected"),
    [
        (17, 0, True, "strong_positive"),
        (16, 0, True, "accept"),
        (15, 0, True, "weak_positive"),
        (15, 1, True, "mixed"),
        (14, 0, True, "reject"),
        (20, 0, False, "invalid"),
    ],
)
def test_preregistered_decision_gate(
    resolved: int, regressions: int, activated: bool, expected: str
) -> None:
    assert _decision(resolved, regressions, activated) == expected


def test_changed_files_uses_unified_diff_headers() -> None:
    patch = (
        b"diff --git a/a.py b/a.py\n"
        b"diff --git a/a.py b/a.py\n"
        b"diff --git a/pkg/b.py b/pkg/b.py\n"
    )
    assert _changed_files(patch) == ["a.py", "pkg/b.py"]


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


def test_treatment_blob_is_frozen() -> None:
    project_root = Path(__file__).resolve().parents[1]
    assert (
        len(
            _git_blob_sha256(
                project_root,
                "563a99fe72b078fa91bfb682d60d6d19f398a864",
                "agent_forge/tools/find_files.py",
            )
        )
        == 64
    )


def test_run_source_tag_is_frozen_without_requiring_current_head() -> None:
    project_root = Path(__file__).resolve().parents[1]
    assert _git_revision(project_root, RUN_SOURCE_TAG) == RUN_SOURCE_COMMIT

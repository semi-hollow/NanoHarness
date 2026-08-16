from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_forge.bench.domain.cohort import load_benchmark_cohort


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHOWCASE_ROOT = PROJECT_ROOT / "benchmarks" / "showcase"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ids_sha256(case_ids: list[str]) -> str:
    return hashlib.sha256("\n".join(case_ids).encode()).hexdigest()


def test_quality_showcase_publishes_the_completed_mini50_result() -> None:
    summary = json.loads(
        (SHOWCASE_ROOT / "canonical-showcase-v1.json").read_text(encoding="utf-8")
    )
    evaluation = summary["canonical_evaluation"]
    profile = summary["current_profile"]

    assert summary["artifact_type"] == "canonical_showcase"
    assert summary["status"] == "completed"
    assert evaluation["evaluation_id"] == "swebench-verified-mini-50-v1"
    assert evaluation["planned"] == 50
    assert evaluation["terminal_accounted"] == 50
    assert evaluation["official_evaluated"] == 44
    assert evaluation["official_resolved"] == 28
    assert evaluation["official_unresolved"] == 16
    assert evaluation["empty_patch"] == 6
    assert evaluation["provider_infra"] == 0
    assert evaluation["runtime_infra"] == 0
    assert evaluation["evaluator_infra"] == 0
    assert evaluation["external_interruption"] == 0
    assert evaluation["evidence_validated"] is True
    assert "not the full 500-case" in evaluation["claim"]

    profile_manifest = PROJECT_ROOT / profile["references"]["profile_manifest"]
    assert profile_manifest.is_file()
    assert _sha256(profile_manifest) == profile["references"]["profile_manifest_sha256"]


def test_mini_50_is_the_fixed_unique_canonical_set() -> None:
    manifest_path = SHOWCASE_ROOT / "swebench-verified-mini-50-v1.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    cohort = load_benchmark_cohort(manifest_path)
    case_ids = list(cohort.case_ids)

    assert len(case_ids) == 50
    assert len(set(case_ids)) == 50
    assert cohort.shard_order == ("all",)
    assert list(cohort.select_shard("all").case_ids) == case_ids
    assert _ids_sha256(case_ids) == payload["selection"]["cohort_sha256"]
    assert payload["selection"]["cohort_sha256"] == (
        "7874edd7eab06ed1be2e5033c1a0b5dc951272864d7dfa789d9cff39675386fc"
    )

    summary = json.loads(
        (SHOWCASE_ROOT / "canonical-showcase-v1.json").read_text(encoding="utf-8")
    )
    assert summary["canonical_evaluation"]["cohort_manifest"] == (
        "benchmarks/showcase/swebench-verified-mini-50-v1.json"
    )
    assert summary["canonical_evaluation"]["official_resolved"] == 28
    assert summary["supporting_checks"] == [
        {
            "id": "fixed-development-10-reference-v1",
            "label": "固定开发样本 10",
            "role": "development_and_regression_only",
            "quality_headline": False,
            "status": "historical_completed",
            "manifest": "benchmarks/experiments/tool-aci-r1/seen-set.json",
            "observed_official_resolved": 4,
            "planned": 10,
        }
    ]


def test_active_showcase_does_not_reference_archived_campaigns() -> None:
    active_text = (SHOWCASE_ROOT / "canonical-showcase-v1.json").read_text(
        encoding="utf-8"
    )

    assert "quality-selection" not in active_text
    assert "canonical-50-v1" not in active_text
    assert "Infrastructure Smoke-5" not in active_text
    assert (PROJECT_ROOT / "benchmarks/archive/legacy-benchmarks").is_dir()

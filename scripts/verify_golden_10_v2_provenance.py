#!/usr/bin/env python3
"""Recompute Golden-10 v2 from two frozen SWE-bench identity columns."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any


SEED = "nanoharness-golden-dev-10-v2"
CANONICAL_BUILDER = Path(__file__).with_name("build_canonical_showcase_cohort.py")


def _canonical() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "canonical_safe_sampler", CANONICAL_BUILDER
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the frozen Canonical sampler")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rank(instance_id: str) -> tuple[str, str]:
    digest = hashlib.sha256(f"{SEED}:{instance_id}".encode("utf-8")).hexdigest()
    return digest, instance_id


def verify(
    arrow_path: Path,
    golden_path: Path,
    canonical_path: Path,
    historical_path: Path,
) -> dict[str, Any]:
    safe = _canonical()
    rows = safe._read_safe_rows(arrow_path)  # only instance_id/repo materialise
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    historical = json.loads(historical_path.read_text(encoding="utf-8"))
    provenance = golden["selection_provenance"]
    pool_claim = provenance["remaining_pool"]
    sources = pool_claim["exclusion_sources"]
    inputs = [
        (historical_path, historical["case_ids"]),
        (canonical_path, canonical["case_ids"]),
    ]
    for claim, (path, ids) in zip(sources, inputs, strict=True):
        if claim != {
            "path": str(path),
            "file_sha256": safe._sha256_file(path),
            "case_count": len(ids),
            "ordered_case_ids_sha256": safe._ids_sha256(ids),
        }:
            raise RuntimeError(f"exclusion source provenance drift: {path}")
    excluded = sorted(set(historical["case_ids"]) | set(canonical["case_ids"]))
    if pool_claim["combined_exclusion_count"] != len(excluded) or pool_claim[
        "combined_exclusion_ordered_case_ids_sha256"
    ] != safe._ids_sha256(excluded):
        raise RuntimeError("combined exclusion provenance drift")
    remaining = sorted(
        (row for row in rows if row["instance_id"] not in set(excluded)),
        key=lambda row: (row["instance_id"], row["repo"]),
    )
    selected = []
    for repo in sorted({row["repo"] for row in remaining}):
        repo_rows = sorted(
            (row for row in remaining if row["repo"] == repo),
            key=lambda row: _rank(row["instance_id"]),
        )
        winner = repo_rows[0]
        selected.append(
            {
                "instance_id": winner["instance_id"],
                "repo": repo,
                "eligible_repo_case_count": len(repo_rows),
                "rank_sha256": _rank(winner["instance_id"])[0],
            }
        )
    pool = {
        "eligible_row_count": len(remaining),
        "eligible_repository_count": len(selected),
        "eligible_ordered_case_ids_sha256": safe._ids_sha256(
            [row["instance_id"] for row in remaining]
        ),
        "eligible_ordered_rows_sha256": hashlib.sha256(
            "\n".join(
                f"{row['instance_id']}\t{row['repo']}" for row in remaining
            ).encode("utf-8")
        ).hexdigest(),
    }
    if any(pool_claim[key] != value for key, value in pool.items()):
        raise RuntimeError("remaining-pool provenance drift")
    case_ids = [item["instance_id"] for item in selected]
    if (
        provenance["seed"] != SEED
        or provenance["allowed_fields"] != ["instance_id", "repo"]
        or golden["selected_cases"] != selected
        or golden["case_ids"] != case_ids
        or golden["ordered_case_ids_sha256"] != safe._ids_sha256(case_ids)
    ):
        raise RuntimeError("Golden-10 v2 is not the mechanical rank-one selection")
    return {
        "valid": True,
        "eligible_rows": len(remaining),
        "eligible_repositories": len(selected),
        "selected_ordered_case_ids_sha256": golden["ordered_case_ids_sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arrow", type=Path, required=True)
    parser.add_argument(
        "--golden", type=Path, default=Path("benchmarks/regression/golden-10-v2.json")
    )
    parser.add_argument(
        "--canonical",
        type=Path,
        default=Path("benchmarks/showcase/canonical-50-v1.json"),
    )
    parser.add_argument(
        "--historical-exclusions",
        type=Path,
        default=Path("benchmarks/showcase/canonical-50-exclusions-v1.json"),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            verify(
                args.arrow.resolve(),
                args.golden,
                args.canonical,
                args.historical_exclusions,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

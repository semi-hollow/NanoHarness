#!/usr/bin/env python3
"""Build the pre-sealed Canonical-50 from a frozen local Arrow file.

Selection is deliberately limited to ``instance_id`` and ``repo``.  The script
selects those two columns before materialising any rows, so patch, tests, hints,
difficulty, and historical outcomes cannot influence membership or order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
COHORT_ID = "canonical-50-v1"
SELECTION_SEED = "nanoharness-canonical-showcase-50-v1"
DATASET_NAME = "princeton-nlp/SWE-bench_Verified"
DATASET_REVISION = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
ARROW_SHA256 = "0d119efe73413554335bd410a04d82fd4a586bfd312cee677ee40af5de2ac46e"
ALLOWED_SELECTION_FIELDS = ("instance_id", "repo")
FORBIDDEN_SELECTION_FIELDS = (
    "problem_statement",
    "hints_text",
    "patch",
    "test_patch",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "difficulty",
    "prior_outcomes",
    "traces",
    "logs",
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ids_sha256(case_ids: list[str]) -> str:
    return _sha256_bytes("\n".join(case_ids).encode("utf-8"))


def _rank(instance_id: str) -> tuple[str, str]:
    digest = _sha256_bytes(f"{SELECTION_SEED}:{instance_id}".encode("utf-8"))
    return digest, instance_id


def _read_safe_rows(arrow_path: Path) -> list[dict[str, str]]:
    """Read only the two public columns used by the sampler."""

    try:
        import pyarrow as pa
        import pyarrow.ipc as ipc
    except ImportError as exc:  # pragma: no cover - actionable CLI boundary
        raise RuntimeError("Install benchmark dependencies with `.[bench]`.") from exc

    if _sha256_file(arrow_path) != ARROW_SHA256:
        raise RuntimeError("frozen Arrow SHA-256 does not match Canonical-50 protocol")
    with pa.memory_map(str(arrow_path), "r") as source:
        try:
            table = ipc.open_file(source).read_all()
        except pa.ArrowInvalid:
            source.seek(0)
            table = ipc.open_stream(source).read_all()
    missing = sorted(set(ALLOWED_SELECTION_FIELDS) - set(table.schema.names))
    if missing:
        raise RuntimeError(f"dataset is missing selection fields: {', '.join(missing)}")
    safe_table = table.select(list(ALLOWED_SELECTION_FIELDS))
    rows = [dict(item) for item in safe_table.to_pylist()]
    if len(rows) != 500:
        raise RuntimeError(f"expected 500 frozen rows, found {len(rows)}")
    instance_ids = [str(row.get("instance_id") or "") for row in rows]
    if not all(instance_ids) or len(set(instance_ids)) != 500:
        raise RuntimeError("frozen dataset instance IDs must be non-empty and unique")
    if any(not str(row.get("repo") or "") for row in rows):
        raise RuntimeError("frozen dataset repository names must be non-empty")
    return [
        {"instance_id": str(row["instance_id"]), "repo": str(row["repo"])}
        for row in rows
    ]


def _load_exclusions(path: Path) -> tuple[set[str], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != 1:
        raise RuntimeError("unsupported Canonical-50 exclusion schema")

    sources = payload.get("source_provenance")
    if not isinstance(sources, list) or len(sources) != 6:
        raise RuntimeError("Canonical-50 exclusions must declare exactly six sources")
    source_names: set[str] = set()
    source_union: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise RuntimeError("Canonical-50 exclusion source must be an object")
        source_name = str(source.get("source") or "")
        if not source_name or source_name in source_names:
            raise RuntimeError("Canonical-50 exclusion source names must be unique")
        source_names.add(source_name)
        source_case_ids = [str(item) for item in source.get("case_ids") or []]
        source_count = int(source.get("case_count") or 0)
        if (
            not all(source_case_ids)
            or len(source_case_ids) != source_count
            or len(set(source_case_ids)) != source_count
        ):
            raise RuntimeError(
                f"Canonical-50 exclusion source count or uniqueness is invalid: "
                f"{source_name}"
            )
        source_expected = str(source.get("ordered_case_ids_sha256") or "")
        if _ids_sha256(source_case_ids) != source_expected:
            raise RuntimeError(
                f"Canonical-50 exclusion source ID SHA-256 is invalid: {source_name}"
            )
        source_union.update(source_case_ids)

    case_ids = [str(item) for item in payload.get("case_ids") or []]
    if len(case_ids) != 117 or len(set(case_ids)) != len(case_ids):
        raise RuntimeError("Canonical-50 exclusions must contain 117 unique IDs")
    expected = str(payload.get("ordered_case_ids_sha256") or "")
    if case_ids != sorted(case_ids) or _ids_sha256(case_ids) != expected:
        raise RuntimeError("Canonical-50 exclusion order or SHA-256 is invalid")
    if sorted(source_union) != case_ids:
        raise RuntimeError(
            "Canonical-50 exclusion source union must exactly equal the 117 sealed IDs"
        )
    return set(case_ids), payload


def _hamilton_quotas(repo_counts: Counter[str]) -> dict[str, int]:
    if len(repo_counts) != 12:
        raise RuntimeError(f"expected 12 repositories, found {len(repo_counts)}")
    remaining_seats = 50 - len(repo_counts)
    adjustable_total = sum(count - 1 for count in repo_counts.values())
    quotas: dict[str, int] = {}
    remainders: dict[str, Fraction] = {}
    for repo, count in repo_counts.items():
        exact = Fraction(remaining_seats * (count - 1), adjustable_total)
        quotas[repo] = 1 + exact.numerator // exact.denominator
        remainders[repo] = exact - int(exact)
    unallocated = 50 - sum(quotas.values())
    for repo in sorted(repo_counts, key=lambda name: (-remainders[name], name))[
        :unallocated
    ]:
        quotas[repo] += 1
    if sum(quotas.values()) != 50:
        raise RuntimeError("Hamilton allocation did not produce 50 seats")
    return quotas


def build_manifest(arrow_path: Path, exclusions_path: Path) -> dict[str, Any]:
    rows = _read_safe_rows(arrow_path)
    exclusions, exclusion_payload = _load_exclusions(exclusions_path)
    eligible = [row for row in rows if row["instance_id"] not in exclusions]
    if len(eligible) != 383:
        raise RuntimeError(f"expected 383 eligible rows, found {len(eligible)}")
    eligible_ids = sorted(row["instance_id"] for row in eligible)
    eligible_sha256 = _ids_sha256(eligible_ids)
    repo_counts = Counter(row["repo"] for row in eligible)
    quotas = _hamilton_quotas(repo_counts)

    selected: list[dict[str, str]] = []
    for repo, quota in quotas.items():
        repo_rows = sorted(
            (row for row in eligible if row["repo"] == repo),
            key=lambda row: _rank(row["instance_id"]),
        )
        selected.extend(repo_rows[:quota])
    selected.sort(key=lambda row: _rank(row["instance_id"]))
    case_ids = [row["instance_id"] for row in selected]
    cohort_sha256 = _ids_sha256(case_ids)
    waves = {
        f"wave-{index + 1}": case_ids[index * 10 : (index + 1) * 10]
        for index in range(5)
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "cohort_id": COHORT_ID,
        "dataset_name": DATASET_NAME,
        "dataset_revision": DATASET_REVISION,
        "split": "test",
        "universe_size": len(eligible),
        "dataset": {
            "row_count": len(rows),
            "arrow_sha256": ARROW_SHA256,
        },
        "selection": {
            "method": "repo_minimum_one_then_hamilton_proportional_seeded_sha256_rank",
            "seed": SELECTION_SEED,
            "allowed_fields": list(ALLOWED_SELECTION_FIELDS),
            "forbidden_fields": list(FORBIDDEN_SELECTION_FIELDS),
            "rank_expression": "sha256(seed + ':' + instance_id), then instance_id",
            "quota_algorithm": (
                "one per repository; remaining 38 proportional to eligible N_r-1 "
                "using Hamilton largest remainder, repo ASCII tie-break"
            ),
            "universe_sha256": eligible_sha256,
            "cohort_sha256": cohort_sha256,
            "repo_eligible_counts": dict(sorted(repo_counts.items())),
            "repo_quotas": dict(sorted(quotas.items())),
            "exclusions_sha256": _sha256_file(exclusions_path),
            "excluded_case_count": len(exclusions),
            "excluded_ordered_case_ids_sha256": exclusion_payload[
                "ordered_case_ids_sha256"
            ],
            "leakage_boundary": (
                "Selection materialised only instance_id and repo; it did not inspect "
                "issue text, hints, patches, tests, difficulty, traces, logs, or outcomes."
            ),
        },
        "shard_order": list(waves),
        "case_ids": case_ids,
        "selected_cases": selected,
        "shards": waves,
        "claim_limits": [
            "Pass@1 applies only to this deterministic 50-case sample.",
            "This sample is not the full SWE-bench Verified benchmark.",
            "Historical experiments and development sets were excluded before ranking.",
            "No correctness rerun, result-based replacement, or denominator reduction is allowed.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arrow", type=Path, required=True)
    parser.add_argument(
        "--exclusions",
        type=Path,
        default=Path("benchmarks/showcase/canonical-50-exclusions-v1.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/showcase/canonical-50-v1.json"),
    )
    args = parser.parse_args()
    manifest = build_manifest(args.arrow.resolve(), args.exclusions.resolve())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "case_count": len(manifest["case_ids"]),
                "cohort_sha256": manifest["selection"]["cohort_sha256"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

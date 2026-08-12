#!/usr/bin/env python3
"""Export separated Agent-visible and sealed official SWE-bench inputs.

The command prints only counts and SHA-256 identities.  It never prints hidden
dataset values, and it writes the full rows only to the explicitly named sealed
official path consumed by the evaluator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ARROW_SHA256 = "0d119efe73413554335bd410a04d82fd4a586bfd312cee677ee40af5de2ac46e"
AGENT_FIELDS = (
    "instance_id",
    "repo",
    "problem_statement",
    "base_commit",
    "version",
    "environment_setup_commit",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_arrow(arrow_path: Path) -> Any:
    try:
        import pyarrow as pa
        import pyarrow.ipc as ipc
    except ImportError as exc:  # pragma: no cover - actionable CLI boundary
        raise RuntimeError("Install benchmark dependencies with `.[bench]`.") from exc

    if _sha256_file(arrow_path) != ARROW_SHA256:
        raise RuntimeError("frozen Arrow SHA-256 does not match showcase protocol")
    with pa.memory_map(str(arrow_path), "r") as source:
        try:
            return ipc.open_file(source).read_all()
        except pa.ArrowInvalid:
            source.seek(0)
            return ipc.open_stream(source).read_all()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arrow", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--agent-output", type=Path, required=True)
    parser.add_argument("--official-output", type=Path, required=True)
    parser.add_argument("--binding-output", type=Path, required=True)
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    case_ids = [str(item) for item in manifest.get("case_ids") or []]
    if not case_ids or len(set(case_ids)) != len(case_ids):
        raise RuntimeError("manifest case_ids must be non-empty and unique")

    table = _read_arrow(args.arrow.resolve())
    missing_fields = sorted(set(AGENT_FIELDS) - set(table.schema.names))
    if missing_fields:
        raise RuntimeError(f"dataset is missing fields: {', '.join(missing_fields)}")

    # Materialise the six-field projection first and validate the complete ordered
    # identity before accessing full rows for the sealed evaluator-only artifact.
    agent_table = table.select(list(AGENT_FIELDS))
    agent_by_id = {
        str(row["instance_id"]): dict(row) for row in agent_table.to_pylist()
    }
    if not set(case_ids).issubset(agent_by_id):
        missing = sorted(set(case_ids) - set(agent_by_id))
        raise RuntimeError(f"manifest Cases missing from dataset: {missing}")
    agent_rows = [agent_by_id[instance_id] for instance_id in case_ids]
    if [str(row["instance_id"]) for row in agent_rows] != case_ids:
        raise RuntimeError("agent dataset order differs from the frozen manifest")

    full_by_id = {
        str(row["instance_id"]): dict(row)
        for row in table.to_pylist()
        if str(row["instance_id"]) in set(case_ids)
    }
    if set(full_by_id) != set(case_ids):
        raise RuntimeError("sealed official dataset is missing a frozen Case")
    full_rows = [full_by_id[instance_id] for instance_id in case_ids]
    for agent_row, full_row in zip(agent_rows, full_rows, strict=True):
        if any(full_row.get(field) != agent_row.get(field) for field in AGENT_FIELDS):
            raise RuntimeError("Agent and sealed official identities differ")

    agent_path = args.agent_output.resolve()
    official_path = args.official_output.resolve()
    binding_path = args.binding_output.resolve()
    _write_json(agent_path, agent_rows)
    _write_json(official_path, full_rows)
    identity_rows = [
        {
            "instance_id": str(row["instance_id"]),
            "repo": str(row["repo"]),
            "base_commit": str(row["base_commit"]),
            "version": str(row["version"]),
            "environment_setup_commit": str(row["environment_setup_commit"]),
            "problem_statement_sha256": hashlib.sha256(
                str(row["problem_statement"]).encode("utf-8")
            ).hexdigest(),
        }
        for row in agent_rows
    ]
    binding = {
        "schema_version": 1,
        "status": "mechanically_exported_no_hidden_values_printed",
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256_file(manifest_path),
        "exporter_sha256": _sha256_file(Path(__file__).resolve()),
        "arrow_sha256": ARROW_SHA256,
        "row_count": len(case_ids),
        "ordered_case_ids": case_ids,
        "agent_fields": list(AGENT_FIELDS),
        "agent_output": str(agent_path),
        "agent_sha256": _sha256_file(agent_path),
        "official_output": str(official_path),
        "official_sha256": _sha256_file(official_path),
        "safe_identities": identity_rows,
        "boundary": (
            "Agent receives only agent_output through --cases-file. The official "
            "evaluator alone receives official_output through --dataset."
        ),
    }
    _write_json(binding_path, binding)
    print(
        json.dumps(
            {
                "binding": str(binding_path),
                "rows": len(case_ids),
                "agent_sha256": binding["agent_sha256"],
                "official_sha256": binding["official_sha256"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

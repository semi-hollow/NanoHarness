"""一次性迁移当前 Workbench evidence closure 中的 TaskCheckpoint v2。

本脚本只处理 ``session_digest -> conversation_history_digest`` 以及由此必然
变化的 artifact hash、byte size 和 evidence-tree digest。它不会枚举或改写
Workbench canonical closure 之外的历史实验。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from apps.workbench.adapters.evidence_files import FileEvidenceCatalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_MANIFEST = Path("benchmarks/showcase/evidence-review-v1.json")
MIGRATION_MANIFEST = Path("migrations/context-semantic-naming-v3-manifest.json")
CHECKPOINT_V2_FIELD = "session_digest"
CHECKPOINT_V3_FIELD = "conversation_history_digest"
CHECKPOINT_IDENTITY_FIELDS = frozenset(
    {
        "run_id",
        "task",
        "workspace",
        "status",
        "current_step",
        "messages_count",
        "observations_count",
    }
)
JSON_SUFFIXES = frozenset({".json", ".jsonl"})


@dataclass(frozen=True)
class JsonArtifact:
    value: Any
    truncated_tail: str = ""


@dataclass(frozen=True)
class MigrationPlan:
    closure: tuple[Path, ...]
    staged: dict[Path, bytes]
    manifest: dict[str, Any]


def derive_workbench_closure(project_root: Path) -> tuple[Path, ...]:
    """从 review manifest 与实际 FileEvidenceCatalog reader 推导 closure。"""

    project_root = project_root.resolve()
    review_path = project_root / REVIEW_MANIFEST
    review = _read_json(review_path)
    sources = review.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("review manifest sources are missing")

    closure: set[Path] = {review_path}
    showcase_root = project_root / ".agent_forge/runs/showcases"
    for source_key in ("governed", "orchestration"):
        configured = sources.get(source_key)
        if not isinstance(configured, dict):
            raise ValueError(f"review source is missing: {source_key}")
        run_name = str(configured.get("canonical_run") or "").strip()
        evidence_tree = configured.get("evidence_tree")
        evidence_tree = evidence_tree if isinstance(evidence_tree, dict) else {}
        patterns = evidence_tree.get("include")
        if not run_name or not isinstance(patterns, list) or not patterns:
            raise ValueError(f"canonical evidence tree is incomplete: {source_key}")
        run_root = showcase_root / run_name
        for pattern in patterns:
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"invalid evidence tree pattern: {source_key}")
            closure.update(
                path.resolve()
                for path in run_root.glob(pattern)
                if path.is_file() and path.suffix in JSON_SUFFIXES
            )

    evaluation = sources.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ValueError("review source is missing: evaluation")
    for key in ("canonical_artifact", "result_artifact", "completion_artifact"):
        _add_declared_json(project_root, closure, evaluation.get(key))
    for representative in evaluation.get("representative_cases") or ():
        if isinstance(representative, dict):
            provenance = representative.get("provenance")
            if isinstance(provenance, dict):
                _add_declared_json(
                    project_root,
                    closure,
                    provenance.get("derived_review"),
                )

    catalog_sources = FileEvidenceCatalog(project_root).evidence_sources()
    overview = next(
        (source for source in catalog_sources if source.key == "evaluation"),
        None,
    )
    if overview is None or not overview.run_key:
        raise ValueError("canonical Mini-50 Workbench source is unavailable")
    for source in catalog_sources:
        if source.category_key != "evaluation" or source.run_key != overview.run_key:
            continue
        _add_path_if_json(project_root, closure, source.primary_path)
        _add_path_if_json(project_root, closure, source.usage_path)
        for _, trace_path in source.trace_entries:
            _add_path_if_json(project_root, closure, trace_path)

    if overview.run_dir is None:
        raise ValueError("canonical Mini-50 resolver root is unavailable")
    combined_result = overview.run_dir / "combined_result.json"
    resolver_input = (
        combined_result
        if combined_result.is_file()
        else overview.run_dir / "campaign.json"
    )
    _add_path_if_json(project_root, closure, resolver_input)
    return tuple(sorted(closure, key=lambda path: _relative(project_root, path)))


def build_migration_plan(project_root: Path) -> MigrationPlan:
    """构造完整内存 write-set；调用方显式选择是否 apply。"""

    project_root = project_root.resolve()
    closure = derive_workbench_closure(project_root)
    original = {path: path.read_bytes() for path in closure}
    staged: dict[Path, bytes] = {}
    transforms: dict[Path, list[str]] = {}
    checkpoint_counts: dict[Path, int] = {}

    for path in closure:
        artifact = _load_artifact(path, original[path])
        migrated, count = _transform_checkpoints(artifact.value)
        if count:
            staged[path] = _dump_artifact(
                path, JsonArtifact(migrated, artifact.truncated_tail)
            )
            transforms[path] = [
                "checkpoint.session_digest -> checkpoint.conversation_history_digest",
                "nested TaskCheckpoint schema_version 2 -> 3",
            ]
            checkpoint_counts[path] = count

    _update_run_manifest_integrity(
        project_root,
        closure,
        original,
        staged,
        transforms,
    )
    _update_trace_hash_references(
        closure,
        original,
        staged,
        transforms,
    )
    tree_updates = _update_evidence_tree_integrity(
        project_root,
        original,
        staged,
        transforms,
    )

    file_entries: list[dict[str, Any]] = []
    for path in sorted(staged, key=lambda item: _relative(project_root, item)):
        old_bytes = original[path]
        new_bytes = staged[path]
        old_artifact = _load_artifact(path, old_bytes)
        new_artifact = _load_artifact(path, new_bytes)
        old_normalized = _semantic_normalize(old_artifact.value)
        new_normalized = _semantic_normalize(new_artifact.value)
        if old_artifact.truncated_tail != new_artifact.truncated_tail:
            raise ValueError(
                f"truncated JSONL tail changed: {_relative(project_root, path)}"
            )
        if old_normalized != new_normalized:
            raise ValueError(
                f"non-schema value changed: {_relative(project_root, path)}"
            )
        semantic_sha = _json_value_sha256(
            {"value": old_normalized, "truncated_tail": old_artifact.truncated_tail}
        )
        old_schema = _root_schema_version(old_artifact.value)
        new_schema = _root_schema_version(new_artifact.value)
        file_entries.append(
            {
                "path": _relative(project_root, path),
                "old_sha256": _sha256_bytes(old_bytes),
                "new_sha256": _sha256_bytes(new_bytes),
                "old_byte_size": len(old_bytes),
                "new_byte_size": len(new_bytes),
                "old_schema_version": old_schema,
                "new_schema_version": new_schema,
                "checkpoint_schema_version": (
                    {"old": 2, "new": 3} if checkpoint_counts.get(path) else None
                ),
                "checkpoint_count": checkpoint_counts.get(path, 0),
                "transform": transforms[path],
                "normalized_semantic_sha256": semantic_sha,
            }
        )

    manifest = {
        "schema_version": 1,
        "migration_id": "context-semantic-naming-v3-2026-08-19",
        "completed_on": "2026-08-19",
        "policy": {
            "scope": "current Workbench canonical evidence closure only",
            "closure_derivation": [
                "benchmarks/showcase/evidence-review-v1.json",
                "FileEvidenceCatalog canonical sources",
                "Workbench review projection declared artifacts",
            ],
            "experiment_rerun": False,
            "production_legacy_fallback": False,
            "archive_modified": False,
        },
        "checkpoint_contract": {
            "old_schema_version": 2,
            "new_schema_version": 3,
            "old_field": CHECKPOINT_V2_FIELD,
            "new_field": CHECKPOINT_V3_FIELD,
        },
        "closure": {
            "inspected_file_count": len(closure),
            "touched_file_count": len(file_entries),
            "write_set_is_subset_of_closure": True,
        },
        "derived_integrity_updates": tree_updates,
        "files": file_entries,
        "preserved_values": [
            "status and timestamps",
            "model outputs and Tool Observations",
            "candidate diffs and execution order",
            "resolved/unresolved/empty-patch outcomes",
            "usage and cost",
            "run identities",
        ],
    }
    manifest_path = project_root / MIGRATION_MANIFEST
    staged[manifest_path] = _dump_json(manifest)
    return MigrationPlan(closure=closure, staged=staged, manifest=manifest)


def apply_migration_plan(project_root: Path, plan: MigrationPlan) -> Path:
    """以同目录原子替换应用 write-set；失败时从临时备份回滚。"""

    project_root = project_root.resolve()
    backup_root = Path(tempfile.mkdtemp(prefix="nanoharness-context-v3-backup-"))
    existing_paths = [path for path in plan.staged if path.is_file()]
    for path in existing_paths:
        backup = backup_root / _relative(project_root, path)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    try:
        manifest_path = project_root / MIGRATION_MANIFEST
        for path in sorted(
            (item for item in plan.staged if item != manifest_path),
            key=lambda item: _relative(project_root, item),
        ):
            _atomic_write_bytes(path, plan.staged[path])
            if path in existing_paths:
                _restore_file_metadata(
                    path,
                    backup_root / _relative(project_root, path),
                )
        _atomic_write_bytes(manifest_path, plan.staged[manifest_path])
        if manifest_path in existing_paths:
            _restore_file_metadata(
                manifest_path,
                backup_root / _relative(project_root, manifest_path),
            )
        else:
            manifest_path.chmod(0o644)
    except Exception:
        for path in existing_paths:
            backup = backup_root / _relative(project_root, path)
            _atomic_write_bytes(path, backup.read_bytes())
            _restore_file_metadata(path, backup)
        manifest_path = project_root / MIGRATION_MANIFEST
        if manifest_path not in existing_paths:
            manifest_path.unlink(missing_ok=True)
        raise
    return backup_root


def verify_applied_migration(project_root: Path) -> dict[str, int]:
    """验证当前文件 hash、v3 checkpoint 与 deterministic evidence tree。"""

    project_root = project_root.resolve()
    manifest = _read_json(project_root / MIGRATION_MANIFEST)
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("migration manifest has no files")
    closure = set(derive_workbench_closure(project_root))
    checkpoint_count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid migration manifest file entry")
        path = (project_root / str(entry.get("path") or "")).resolve()
        if path not in closure:
            raise ValueError(f"migrated file is outside current closure: {path}")
        if _sha256_bytes(path.read_bytes()) != entry.get("new_sha256"):
            raise ValueError(f"new hash mismatch: {_relative(project_root, path)}")
        artifact = _load_artifact(path, path.read_bytes())
        if _count_checkpoint_field(artifact.value, CHECKPOINT_V2_FIELD):
            raise ValueError(
                f"legacy checkpoint field remains: {_relative(project_root, path)}"
            )
        count = _count_checkpoint_field(artifact.value, CHECKPOINT_V3_FIELD)
        checkpoint_count += count
        if count and _has_checkpoint_schema(artifact.value, 2):
            raise ValueError(f"v2 checkpoint remains: {_relative(project_root, path)}")
        normalized_sha = _json_value_sha256(
            {
                "value": _semantic_normalize(artifact.value),
                "truncated_tail": artifact.truncated_tail,
            }
        )
        if normalized_sha != entry.get("normalized_semantic_sha256"):
            raise ValueError(
                f"semantic payload mismatch: {_relative(project_root, path)}"
            )

    review = _read_json(project_root / REVIEW_MANIFEST)
    for source_key in ("governed", "orchestration"):
        configured = review["sources"][source_key]
        root = (
            project_root / ".agent_forge/runs/showcases" / configured["canonical_run"]
        )
        count, digest = _evidence_tree(
            root,
            tuple(configured["evidence_tree"]["include"]),
        )
        if count != configured["evidence_tree"]["file_count"]:
            raise ValueError(f"evidence tree file count mismatch: {source_key}")
        if digest != configured["evidence_tree"]["sha256"]:
            raise ValueError(f"evidence tree hash mismatch: {source_key}")
    return {
        "closure_files": len(closure),
        "migrated_files": len(entries),
        "checkpoint_objects": checkpoint_count,
    }


def _update_run_manifest_integrity(
    project_root: Path,
    closure: tuple[Path, ...],
    original: dict[Path, bytes],
    staged: dict[Path, bytes],
    transforms: dict[Path, list[str]],
) -> None:
    for manifest_path in (path for path in closure if path.name == "run_manifest.json"):
        manifest = _load_artifact(
            manifest_path,
            staged.get(manifest_path, original[manifest_path]),
        ).value
        if not isinstance(manifest, dict):
            raise ValueError(f"run manifest is not an object: {manifest_path}")
        changed = False
        for artifact in manifest.get("artifacts") or ():
            if not isinstance(artifact, dict):
                continue
            relative_path = str(artifact.get("relative_path") or "")
            child = (manifest_path.parent / relative_path).resolve()
            if child not in staged:
                continue
            artifact["sha256"] = _sha256_bytes(staged[child])
            artifact["byte_size"] = len(staged[child])
            changed = True
        if changed:
            staged[manifest_path] = _dump_json(manifest)
            transforms.setdefault(manifest_path, []).append(
                "referenced artifact sha256 and byte_size refreshed"
            )


def _update_trace_hash_references(
    closure: tuple[Path, ...],
    original: dict[Path, bytes],
    staged: dict[Path, bytes],
    transforms: dict[Path, list[str]],
) -> None:
    hash_map = {
        _sha256_bytes(original[path]): _sha256_bytes(new_bytes)
        for path, new_bytes in staged.items()
        if path.name == "trace.json"
    }
    if not hash_map:
        return
    for path in closure:
        if path.suffix != ".json":
            continue
        current = staged.get(path, original[path]).decode("utf-8")
        replaced = current
        count = 0
        for old_sha256, new_sha256 in hash_map.items():
            occurrences = replaced.count(old_sha256)
            if occurrences:
                replaced = replaced.replace(old_sha256, new_sha256)
                count += occurrences
        if not count:
            continue
        staged[path] = replaced.encode("utf-8")
        transforms.setdefault(path, []).append(
            f"{count} referenced trace_sha256 value(s) refreshed"
        )


def _update_evidence_tree_integrity(
    project_root: Path,
    original: dict[Path, bytes],
    staged: dict[Path, bytes],
    transforms: dict[Path, list[str]],
) -> list[dict[str, Any]]:
    review_path = project_root / REVIEW_MANIFEST
    review = _load_artifact(
        review_path, staged.get(review_path, original[review_path])
    ).value
    if not isinstance(review, dict):
        raise ValueError("review manifest is not an object")
    updates: list[dict[str, Any]] = []
    for source_key in ("governed", "orchestration"):
        configured = review["sources"][source_key]
        run_root = (
            project_root / ".agent_forge/runs/showcases" / configured["canonical_run"]
        )
        patterns = tuple(configured["evidence_tree"]["include"])
        old_count, old_digest = _evidence_tree(run_root, patterns)
        expected = configured["evidence_tree"]
        if old_count != expected["file_count"] or old_digest != expected["sha256"]:
            raise ValueError(f"pre-migration evidence tree mismatch: {source_key}")
        new_count, new_digest = _evidence_tree(run_root, patterns, staged=staged)
        expected["file_count"] = new_count
        expected["sha256"] = new_digest
        canonical_path = run_root / configured["canonical_artifact"]
        old_canonical = _sha256_bytes(
            original.get(canonical_path, canonical_path.read_bytes())
        )
        new_canonical = _sha256_bytes(
            staged.get(canonical_path, canonical_path.read_bytes())
        )
        if configured.get("canonical_sha256") != old_canonical:
            raise ValueError(f"pre-migration canonical hash mismatch: {source_key}")
        configured["canonical_sha256"] = new_canonical
        updates.append(
            {
                "source": source_key,
                "file_count": new_count,
                "old_evidence_tree_sha256": old_digest,
                "new_evidence_tree_sha256": new_digest,
                "old_canonical_sha256": old_canonical,
                "new_canonical_sha256": new_canonical,
            }
        )
    staged[review_path] = _dump_json(review)
    transforms.setdefault(review_path, []).append(
        "canonical evidence-tree and referenced trace hashes refreshed"
    )
    return updates


def _transform_checkpoints(value: Any) -> tuple[Any, int]:
    if isinstance(value, list):
        output: list[Any] = []
        count = 0
        for item in value:
            migrated, item_count = _transform_checkpoints(item)
            output.append(migrated)
            count += item_count
        return output, count
    if not isinstance(value, dict):
        return value, 0

    is_v2_checkpoint = _is_checkpoint(value, CHECKPOINT_V2_FIELD)
    if is_v2_checkpoint and CHECKPOINT_V3_FIELD in value:
        raise ValueError("checkpoint contains both v2 and v3 digest fields")
    if is_v2_checkpoint and value.get("schema_version") != 2:
        raise ValueError("session_digest checkpoint is not schema version 2")

    output: dict[str, Any] = {}
    count = 0
    for key, item in value.items():
        next_key = (
            CHECKPOINT_V3_FIELD
            if is_v2_checkpoint and key == CHECKPOINT_V2_FIELD
            else key
        )
        next_item = 3 if is_v2_checkpoint and key == "schema_version" else item
        migrated, item_count = _transform_checkpoints(next_item)
        output[next_key] = migrated
        count += item_count
    return output, count + int(is_v2_checkpoint)


def _semantic_normalize(value: Any) -> Any:
    """反向归一化新字段，并忽略这轮唯一允许变化的 integrity metadata。"""

    if isinstance(value, list):
        return [_semantic_normalize(item) for item in value]
    if not isinstance(value, dict):
        return value
    is_v3_checkpoint = _is_checkpoint(value, CHECKPOINT_V3_FIELD)
    output: dict[str, Any] = {}
    for key, item in value.items():
        if key == "sha256" or key.endswith("_sha256") or key == "byte_size":
            continue
        next_key = (
            CHECKPOINT_V2_FIELD
            if is_v3_checkpoint and key == CHECKPOINT_V3_FIELD
            else key
        )
        next_item = 2 if is_v3_checkpoint and key == "schema_version" else item
        output[next_key] = _semantic_normalize(next_item)
    return output


def _is_checkpoint(value: dict[str, Any], digest_field: str) -> bool:
    return digest_field in value and CHECKPOINT_IDENTITY_FIELDS <= value.keys()


def _count_checkpoint_field(value: Any, digest_field: str) -> int:
    if isinstance(value, list):
        return sum(_count_checkpoint_field(item, digest_field) for item in value)
    if not isinstance(value, dict):
        return 0
    return int(_is_checkpoint(value, digest_field)) + sum(
        _count_checkpoint_field(item, digest_field) for item in value.values()
    )


def _has_checkpoint_schema(value: Any, schema_version: int) -> bool:
    if isinstance(value, list):
        return any(_has_checkpoint_schema(item, schema_version) for item in value)
    if not isinstance(value, dict):
        return False
    if (
        _is_checkpoint(value, CHECKPOINT_V2_FIELD)
        or _is_checkpoint(value, CHECKPOINT_V3_FIELD)
    ) and value.get("schema_version") == schema_version:
        return True
    return any(_has_checkpoint_schema(item, schema_version) for item in value.values())


def _load_artifact(path: Path, payload: bytes) -> JsonArtifact:
    text = payload.decode("utf-8")
    if path.suffix == ".json":
        return JsonArtifact(json.loads(text))
    records: list[Any] = []
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if index != len(lines) - 1:
                raise ValueError(f"intermediate JSONL record is corrupt: {path}")
            return JsonArtifact(records, line)
    return JsonArtifact(records)


def _dump_artifact(path: Path, artifact: JsonArtifact) -> bytes:
    if path.suffix == ".json":
        return _dump_json(artifact.value)
    lines = [json.dumps(item, ensure_ascii=False) + "\n" for item in artifact.value]
    if artifact.truncated_tail:
        lines.append(artifact.truncated_tail)
    return "".join(lines).encode("utf-8")


def _dump_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _root_schema_version(value: Any) -> int | None:
    if isinstance(value, dict) and isinstance(value.get("schema_version"), int):
        return int(value["schema_version"])
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("schema_version"), int):
                return int(item["schema_version"])
    return None


def _add_declared_json(project_root: Path, closure: set[Path], value: Any) -> None:
    text = str(value or "").strip()
    if text:
        _add_path_if_json(project_root, closure, project_root / text)


def _add_path_if_json(
    project_root: Path,
    closure: set[Path],
    path: Path | None,
) -> None:
    if path is None or not path.is_file() or path.suffix not in JSON_SUFFIXES:
        return
    resolved = path.resolve()
    try:
        resolved.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ValueError(
            f"Workbench evidence escapes project root: {resolved}"
        ) from exc
    closure.add(resolved)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _evidence_tree(
    root: Path,
    patterns: tuple[str, ...],
    *,
    staged: dict[Path, bytes] | None = None,
) -> tuple[int, str]:
    files = {
        path.resolve()
        for pattern in patterns
        for path in root.glob(pattern)
        if path.is_file()
    }
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(
            _sha256_bytes((staged or {}).get(path, path.read_bytes())).encode("ascii")
        )
        digest.update(b"\n")
    return len(files), digest.hexdigest()


def _json_value_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(payload)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _relative(project_root: Path, path: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_file_metadata(path: Path, reference: Path) -> None:
    source_stat = reference.stat()
    path.chmod(source_stat.st_mode & 0o7777)
    os.utime(
        path,
        ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
    )


def _format_summary(summary: dict[str, int]) -> str:
    return " · ".join(f"{key}={value}" for key, value in summary.items())


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply", action="store_true", help="apply the one-time migration"
    )
    mode.add_argument(
        "--verify", action="store_true", help="verify an applied migration"
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args(list(argv) if argv is not None else None)
    project_root = args.project_root.resolve()
    if args.verify:
        print(_format_summary(verify_applied_migration(project_root)))
        return 0
    plan = build_migration_plan(project_root)
    print(
        _format_summary(
            {
                "closure_files": len(plan.closure),
                "touched_files": len(plan.manifest["files"]),
                "checkpoint_objects": sum(
                    int(item["checkpoint_count"]) for item in plan.manifest["files"]
                ),
            }
        )
    )
    if not args.apply:
        print("dry-run only; pass --apply to write the migration")
        return 0
    backup_root = apply_migration_plan(project_root, plan)
    print(f"backup_root={backup_root}")
    print(_format_summary(verify_applied_migration(project_root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

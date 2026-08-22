"""从版本化实验目录构建 Workbench 的只读实验目录。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from apps.workbench.domain import ExperimentBundle, ExperimentSource
from apps.workbench.ports import ExperimentCatalogPort

_VIEW_ITEMS = (
    ("overview", "实验概览", "overview"),
    ("variables", "变量与实现", "variables"),
    ("results", "结果对比", "results"),
    ("evidence", "证据与边界", "evidence"),
)

_TRANSITION_LABELS = {
    "unresolved_to_resolved": "Gain ↑",
    "resolved_to_unresolved": "Regression ↓",
    "resolved_to_resolved": "保持 Resolved",
    "unresolved_to_unresolved": "保持 Unresolved",
}

_MEASUREMENT_LABELS = {
    "official_resolved": "Official Resolved",
    "official_unresolved": "Official Unresolved",
    "agent_terminal_empty_patch": "Agent Empty Patch",
}


class FileExperimentCatalog(ExperimentCatalogPort):
    """只扫描显式 ``experiment.json``，历史 archive 不会自动进入主视图。"""

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir.resolve()
        self._root = self._project_dir / "benchmarks" / "experiments"
        self._history_path = (
            self._project_dir
            / "benchmarks"
            / "showcase"
            / "engineering-history-v1.json"
        )

    def experiment_sources(self) -> tuple[ExperimentSource, ...]:
        sources: list[tuple[int, ExperimentSource]] = []
        for manifest_path in sorted(self._root.glob("*/experiment.json")):
            manifest = _read_json_object(manifest_path)
            if not _valid_manifest(manifest) or manifest.get("active") is not True:
                continue
            result = self._read_manifest_artifact(manifest, "result")
            if result is None:
                continue
            order = _integer(manifest.get("order"), default=999)
            sources.extend(
                (order, source)
                for source in self._sources_for_manifest(
                    manifest_path, manifest, result
                )
            )
        sources.extend(self._history_sources())
        return tuple(
            source
            for _, source in sorted(
                sources,
                key=lambda entry: (
                    entry[0],
                    entry[1].comparison_title,
                    _item_sort_key(entry[1]),
                ),
            )
        )

    def experiment_bundle(self, source_key: str) -> ExperimentBundle | None:
        source = next(
            (item for item in self.experiment_sources() if item.key == source_key),
            None,
        )
        if source is None:
            return None
        if source.experiment_kind == "historical":
            return self._history_bundle(source)
        manifest = _read_json_object(source.manifest_path)
        plan = self._read_manifest_artifact(manifest, "plan")
        result = self._read_manifest_artifact(manifest, "result")
        if plan is None or result is None:
            return None
        provenance = _read_json_object(self._root / "artifact-provenance.json")
        artifacts: list[tuple[str, Path]] = []
        paths = manifest.get("paths")
        if isinstance(paths, dict):
            for role in (
                "plan",
                "result",
                "readme",
                "report",
                "case_order",
                "failure_review",
            ):
                path = self._resolve_path(paths.get(role))
                if path is not None and path.is_file():
                    artifacts.append((role, path))
            executions = paths.get("executions")
            if isinstance(executions, list):
                for index, raw_path in enumerate(executions, start=1):
                    path = self._resolve_path(raw_path)
                    if path is not None and path.is_file():
                        artifacts.append((f"execution_{index}", path))
        return ExperimentBundle(
            source=source,
            manifest=manifest,
            plan=plan,
            result=result,
            provenance=provenance,
            artifacts=tuple(artifacts),
        )

    def _history_sources(self) -> list[tuple[int, ExperimentSource]]:
        history = _read_json_object(self._history_path)
        if not _valid_history(history):
            return []
        sources: list[tuple[int, ExperimentSource]] = []
        for entry in history["entries"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                continue
            if self._validated_history_source(entry) is None:
                continue
            experiment_id = str(entry["id"])
            decision = _mapping(entry.get("decision"))
            sources.append(
                (
                    _integer(entry.get("order"), default=999) + 1000,
                    ExperimentSource(
                        key=f"engineering-history:{experiment_id}:overview",
                        family_key="engineering-history",
                        family_title="ENGINEERING HISTORY · 历史工程实验",
                        comparison_key=experiment_id,
                        comparison_title=str(entry.get("title") or experiment_id),
                        item_key="overview",
                        item_title="历史实验概览",
                        item_kind="overview",
                        experiment_id=experiment_id,
                        experiment_kind="historical",
                        title=str(entry.get("title") or experiment_id),
                        description=str(entry.get("question") or ""),
                        status=str(decision.get("status") or "historical"),
                        decision=str(decision.get("status") or "historical"),
                        manifest_path=self._history_path,
                    ),
                )
            )
        return sources

    def _history_bundle(self, source: ExperimentSource) -> ExperimentBundle | None:
        history = _read_json_object(self._history_path)
        entry = next(
            (
                item
                for item in history.get("entries", [])
                if isinstance(item, dict) and item.get("id") == source.experiment_id
            ),
            None,
        )
        if not isinstance(entry, dict):
            return None
        source_path = self._validated_history_source(entry)
        if source_path is None:
            return None
        manifest = {
            "question": entry.get("question"),
            "decision": entry.get("decision"),
            "boundaries": entry.get("claim_boundaries"),
            "labels": entry.get("labels"),
            "source_path": entry.get("source_path"),
            "source_sha256": entry.get("source_sha256"),
            "treatment": entry.get("treatment"),
            "engineering_lesson": entry.get("engineering_lesson"),
        }
        return ExperimentBundle(
            source=source,
            manifest=manifest,
            plan={},
            result={"observed_results": entry.get("observed_results", [])},
            provenance={
                "schema_version": history.get("schema_version"),
                "evidence_policy": history.get("evidence_policy"),
            },
            artifacts=(
                ("archived_readme", source_path),
                ("derived_history", self._history_path),
            ),
        )

    def _validated_history_source(self, entry: dict[str, Any]) -> Path | None:
        source_path = self._resolve_path(entry.get("source_path"))
        expected_hash = entry.get("source_sha256")
        if (
            source_path is None
            or not source_path.is_file()
            or source_path.is_symlink()
            or not isinstance(expected_hash, str)
        ):
            return None
        try:
            actual_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        except OSError:
            return None
        return source_path if actual_hash == expected_hash else None

    def _sources_for_manifest(
        self,
        manifest_path: Path,
        manifest: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[ExperimentSource, ...]:
        family = _mapping(manifest.get("family"))
        comparison = _mapping(manifest.get("comparison"))
        decision = _mapping(manifest.get("decision"))
        experiment_id = str(manifest["experiment_id"])
        common: dict[str, Any] = {
            "family_key": str(family.get("id") or experiment_id),
            "family_title": str(family.get("title") or manifest.get("title")),
            "comparison_key": str(comparison.get("id") or experiment_id),
            "comparison_title": str(comparison.get("title") or manifest.get("title")),
            "experiment_id": experiment_id,
            "experiment_kind": str(manifest.get("experiment_kind") or "unknown"),
            "title": str(manifest.get("title") or experiment_id),
            "description": str(manifest.get("question") or ""),
            "status": str(result.get("status") or decision.get("status") or "unknown"),
            "decision": str(decision.get("status") or "unknown"),
            "manifest_path": manifest_path,
        }
        sources = [
            ExperimentSource(
                key=f"{experiment_id}:{item_key}",
                item_key=item_key,
                item_title=item_title,
                item_kind=item_kind,
                **common,
            )
            for item_key, item_title, item_kind in _VIEW_ITEMS
        ]
        if manifest.get("experiment_kind") == "paired_ab":
            paired = _mapping(result.get("paired"))
            transitions = paired.get("transitions")
            if isinstance(transitions, list):
                for transition in transitions:
                    if not isinstance(transition, dict):
                        continue
                    case_id = str(transition.get("instance_id") or "")
                    if not case_id:
                        continue
                    transition_name = str(transition.get("transition") or "")
                    label = _TRANSITION_LABELS.get(transition_name, transition_name)
                    sources.append(
                        ExperimentSource(
                            key=f"{experiment_id}:case:{case_id}",
                            item_key=f"case:{case_id}",
                            item_title=f"{label} · {case_id}",
                            item_kind="case",
                            case_id=case_id,
                            **common,
                        )
                    )
        else:
            for case_id, outcome in self._measurement_cases(manifest, result):
                sources.append(
                    ExperimentSource(
                        key=f"{experiment_id}:case:{case_id}",
                        item_key=f"case:{case_id}",
                        item_title=f"{_MEASUREMENT_LABELS.get(outcome, outcome)} · {case_id}",
                        item_kind="case",
                        case_id=case_id,
                        **common,
                    )
                )
        return tuple(sources)

    def _measurement_cases(
        self,
        manifest: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[tuple[str, str], ...]:
        outcomes: dict[str, str] = {}
        case_groups = result.get("case_ids")
        if isinstance(case_groups, dict):
            for outcome, case_ids in case_groups.items():
                if isinstance(case_ids, list):
                    outcomes.update(
                        (str(case_id), str(outcome)) for case_id in case_ids
                    )
        paths = _mapping(manifest.get("paths"))
        order_path = self._resolve_path(paths.get("case_order"))
        order = _read_json_object(order_path).get("case_ids") if order_path else None
        if not isinstance(order, list):
            order = list(outcomes)
        return tuple(
            (str(case_id), outcomes[str(case_id)])
            for case_id in order
            if str(case_id) in outcomes
        )

    def _read_manifest_artifact(
        self,
        manifest: dict[str, Any],
        role: str,
    ) -> dict[str, Any] | None:
        paths = manifest.get("paths")
        if not isinstance(paths, dict):
            return None
        path = self._resolve_path(paths.get(role))
        if path is None or not path.is_file():
            return None
        value = _read_json_object(path)
        return value or None

    def _resolve_path(self, raw_path: object) -> Path | None:
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        path = Path(raw_path)
        if path.is_absolute():
            return None
        candidate = (self._project_dir / path).resolve()
        try:
            candidate.relative_to(self._project_dir)
        except ValueError:
            return None
        return candidate


def _read_json_object(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file() or path.is_symlink():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _valid_manifest(value: dict[str, Any]) -> bool:
    return (
        value.get("schema_version") == 1
        and value.get("artifact_type") == "nanoharness_experiment_view"
        and isinstance(value.get("experiment_id"), str)
        and isinstance(value.get("family"), dict)
        and isinstance(value.get("comparison"), dict)
        and isinstance(value.get("paths"), dict)
    )


def _valid_history(value: dict[str, Any]) -> bool:
    return (
        value.get("schema_version") == 1
        and value.get("artifact_type")
        == "nanoharness_engineering_history_projection"
        and isinstance(value.get("entries"), list)
    )


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _integer(value: object, *, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _item_sort_key(source: ExperimentSource) -> tuple[int, int, str]:
    fixed = {"overview": 0, "variables": 1, "results": 2, "evidence": 3}
    if source.item_key in fixed:
        return (0, fixed[source.item_key], source.item_title)
    return (1, 0, source.item_title)

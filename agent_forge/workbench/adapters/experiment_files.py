"""从版本化实验目录构建 Workbench 的只读实验目录。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_forge.workbench.domain import ExperimentBundle, ExperimentSource
from agent_forge.workbench.ports import ExperimentCatalogPort

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


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _integer(value: object, *, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _item_sort_key(source: ExperimentSource) -> tuple[int, int, str]:
    fixed = {"overview": 0, "variables": 1, "results": 2, "evidence": 3}
    if source.item_key in fixed:
        return (0, fixed[source.item_key], source.item_title)
    return (1, 0, source.item_title)

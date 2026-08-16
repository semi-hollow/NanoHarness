"""Workbench 的实验对比只读模型。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, kw_only=True)
class ExperimentSource:
    """实验导航树中的一个可选对象。"""

    key: str
    family_key: str
    family_title: str
    comparison_key: str
    comparison_title: str
    item_key: str
    item_title: str
    item_kind: str
    experiment_id: str
    experiment_kind: str
    title: str
    description: str
    status: str
    decision: str
    manifest_path: Path
    case_id: str = ""

    @property
    def available(self) -> bool:
        return self.manifest_path.is_file()

    def to_public_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "family_key": self.family_key,
            "family_title": self.family_title,
            "comparison_key": self.comparison_key,
            "comparison_title": self.comparison_title,
            "item_key": self.item_key,
            "item_title": self.item_title,
            "item_kind": self.item_kind,
            "experiment_id": self.experiment_id,
            "experiment_kind": self.experiment_kind,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "decision": self.decision,
            "available": self.available,
            "case_id": self.case_id,
        }


@dataclass(frozen=True, kw_only=True)
class ExperimentBundle:
    """一次实验视图所需的已解析资产。"""

    source: ExperimentSource
    manifest: dict[str, Any]
    plan: dict[str, Any]
    result: dict[str, Any]
    provenance: dict[str, Any]
    artifacts: tuple[tuple[str, Path], ...]

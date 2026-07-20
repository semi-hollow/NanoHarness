"""Workbench HTTP controller 使用的显式依赖集合。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_forge.workbench.ports import EvidenceCatalogPort


@dataclass(frozen=True)
class WorkbenchServices:
    """把 project root 与只读 evidence 查询作为显式依赖传给 UI。"""

    project_dir: Path
    evidence: EvidenceCatalogPort

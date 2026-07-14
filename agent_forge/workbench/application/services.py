"""Workbench HTTP controller 使用的显式依赖集合。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_forge.workbench.ports import BackgroundJobsPort, EvidenceCatalogPort


@dataclass(frozen=True)
class WorkbenchServices:
    """把 project root、evidence 查询和后台任务作为显式依赖传给 UI。"""

    project_dir: Path
    evidence: EvidenceCatalogPort
    jobs: BackgroundJobsPort

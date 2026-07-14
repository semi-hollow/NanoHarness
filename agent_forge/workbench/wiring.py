from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_forge.workbench.adapters.background_jobs import BackgroundJobRunner
from agent_forge.workbench.adapters.evidence_files import (
    FileEvidenceCatalog,
    read_json_file,
)
from agent_forge.workbench.application.services import WorkbenchServices
from agent_forge.workbench.ports import EvidenceCatalogPort


def build_evidence_catalog(project_dir: Path) -> EvidenceCatalogPort:
    return FileEvidenceCatalog(project_dir)


def build_workbench_services(project_dir: Path) -> WorkbenchServices:
    return WorkbenchServices(
        project_dir=project_dir,
        evidence=FileEvidenceCatalog(project_dir),
        jobs=BackgroundJobRunner(project_dir),
    )


def read_evidence_json(path: Path | None) -> dict[str, Any]:
    return read_json_file(path)
